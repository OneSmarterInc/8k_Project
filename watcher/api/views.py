from rest_framework.decorators import api_view
from rest_framework.response import Response

from watcher.models import Filing, AutomationRun
from .serializers import FilingSerializer, AutomationRunSerializer


@api_view(["GET"])
def runs(request):
    queryset = AutomationRun.objects.all().order_by("-started_at")
    serializer = AutomationRunSerializer(queryset, many=True)
    return Response(serializer.data)


import threading
from django.core.management import call_command
import sys

class TeeStream:
    def __init__(self, stream1, stream2):
        self.stream1 = stream1
        self.stream2 = stream2
        
    def write(self, data):
        self.stream1.write(data)
        self.stream2.write(data)
        self.stream1.flush()
        self.stream2.flush()
        
    def flush(self):
        self.stream1.flush()
        self.stream2.flush()

def _run_watcher(daily_chronicle=True):
    try:
        args = ["--auto-index"]
        if not daily_chronicle:
            args.append("--no-daily-chronicle")
            
        print(f"\n--- [AUTOMATION] Starting: python manage.py watcher {' '.join(args)} ---")
        import os
        from django.conf import settings
        log_path = os.path.join(settings.BASE_DIR, "watcher_latest.log")
        
        with open(log_path, "w") as f:
            f.write("Starting watcher...\n")
            
            tee_out = TeeStream(sys.stdout, f)
            tee_err = TeeStream(sys.stderr, f)
            
            call_command("watcher", *args, stdout=tee_out, stderr=tee_err)
            f.write("\nWatcher finished.\n")
            
        print("--- [AUTOMATION] Watcher run finished. Waiting for next poll... ---\n")
    except Exception as e:
        print(f"Error running watcher: {e}")

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
@api_view(["POST"])
def trigger_run(request):
    # Only start if one is not already running
    running = AutomationRun.objects.filter(status=AutomationRun.Status.RUNNING).exists()
    if running:
        return Response({"status": "already_running"})
        
    daily_chronicle = request.data.get("daily_chronicle", True)

    thread = threading.Thread(target=_run_watcher, args=(daily_chronicle,))
    thread.daemon = True
    thread.start()
    return Response({"status": "started"})


@api_view(["GET"])
def run_logs(request):
    import os
    from django.conf import settings
    log_path = os.path.join(settings.BASE_DIR, "watcher_latest.log")
    try:
        with open(log_path, "r") as f:
            content = f.read()
        return Response({"logs": content})
    except FileNotFoundError:
        return Response({"logs": ""})


@api_view(["GET"])
def filings(request):
    queryset = (
        Filing.objects
        .select_related("company")
        .order_by("-created_at")
    )

    import datetime
    
    status_param = request.GET.get("status")
    if status_param == "failed":
        queryset = queryset.filter(ingestion_status="failed")
    else:
        # By default, only show filings that successfully generated a summary and are from Sept 14, 2026 onward
        start_date = datetime.datetime(2026, 9, 14, tzinfo=datetime.timezone.utc)
        queryset = queryset.filter(
            summary_cache__isnull=False,
            accepted_at__gte=start_date
        )

    ticker = request.GET.get("ticker")
    form = request.GET.get("form")

    if ticker:
        queryset = queryset.filter(company__ticker__iexact=ticker)

    if form:
        queryset = queryset.filter(form=form)

    serializer = FilingSerializer(queryset, many=True)
    return Response(serializer.data)

import os
import smtplib
from email.message import EmailMessage
from django.conf import settings
from rest_framework.views import APIView

class SMTPConfigView(APIView):
    def post(self, request):
        action = request.GET.get("action")
        data = request.data
        
        if action == "test":
            host = data.get("smtpHost")
            port = int(data.get("smtpPort", 587))
            user = data.get("smtpUsername")
            password = data.get("smtpPassword")
            sender = data.get("senderEmail")
            recipient = data.get("senderEmail") # Send to self for test
            
            msg = EmailMessage()
            msg.set_content("This is a test email from the SEC Agentic Watcher to verify SMTP settings.")
            msg["Subject"] = "Test Email - SEC Watcher"
            msg["From"] = sender
            msg["To"] = recipient
            
            try:
                server = smtplib.SMTP(host, port, timeout=10)
                if data.get("securityProtocol") == "TLS" or data.get("securityProtocol") == "STARTTLS":
                    server.starttls()
                # SSL might need smtplib.SMTP_SSL, but keeping it simple as per original
                server.login(user, password)
                server.send_message(msg)
                server.quit()
                return Response({"status": "success", "message": "Test email sent successfully!"})
            except Exception as e:
                return Response({"status": "error", "message": str(e)})
                
        elif action == "save":
            env_path = os.path.join(settings.BASE_DIR, ".env")
            
            # Read existing
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8-sig") as f:
                    lines = f.readlines()
            
            # Prepare updates mapping
            updates = {
                "SMTP_SENDER_NAME": data.get("senderName"),
                "SMTP_HOST": data.get("smtpHost"),
                "SMTP_PORT": data.get("smtpPort"),
                "SMTP_SECURITY": data.get("securityProtocol"),
                "SMTP_USERNAME": data.get("smtpUsername"),
                "SMTP_SENDER_EMAIL": data.get("senderEmail"),
                "SMTP_REPLY_TO_EMAIL": data.get("replyToEmail")
            }
            if data.get("smtpPassword"):
                updates["SMTP_PASSWORD"] = data.get("smtpPassword")
                
            new_lines = []
            updated_keys = set()
            
            for line in lines:
                if "=" in line and not line.strip().startswith("#"):
                    k, _ = line.split("=", 1)
                    k = k.strip()
                    if k in updates:
                        new_lines.append(f"{k}={updates[k]}\n")
                        updated_keys.add(k)
                        continue
                new_lines.append(line)
                
            # Add missing keys
            for k, v in updates.items():
                if k not in updated_keys and v:
                    if not new_lines or not new_lines[-1].endswith("\n"):
                        new_lines.append("\n")
                    new_lines.append(f"{k}={v}\n")
                    
            with open(env_path, "w", encoding="utf-8-sig") as f:
                f.writelines(new_lines)
                
            return Response({"status": "success", "message": "Configuration saved to .env"})
            
        return Response({"status": "error", "message": "Invalid action"}, status=400)