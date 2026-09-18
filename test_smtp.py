import os
import smtplib
from email.message import EmailMessage

host = "smtp.ionos.com"
port = 587
user = "akshay.kumar@onesmarter.com"
password = "ewXtLJrSPXUdhubnMtMUT9xXFZ73uwHz"
sender = "support@onesmarter.com"
recipient = "hansavahinib@gmail.com"

msg = EmailMessage()
msg.set_content("This is a test email from the SEC Agentic Watcher to verify SMTP settings.")
msg["Subject"] = "Test Email - SEC Watcher"
msg["From"] = sender
msg["To"] = recipient

try:
    print(f"Connecting to {host}:{port}...")
    server = smtplib.SMTP(host, port, timeout=10)
    server.set_debuglevel(1)
    server.starttls()
    print("Authenticating...")
    server.login(user, password)
    print("Sending email...")
    server.send_message(msg)
    server.quit()
    print("SUCCESS: Test email sent successfully!")
except Exception as e:
    print(f"FAILED: {e}")
