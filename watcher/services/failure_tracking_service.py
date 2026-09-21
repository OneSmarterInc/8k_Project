from watcher.knowledge_base.models import FailureEvent


class FailureTrackingService:
    """
    Central service for recording structured failures.

    Existing failure handling is not replaced.
    This only adds audit tracking.
    """

    @staticmethod
    def record(
        *,
        filing=None,
        stage,
        code,
        message,
    ):

        return FailureEvent.objects.create(
            filing=filing,
            stage=stage,
            code=code,
            message=str(message),
        )

    @staticmethod
    def resolve(*, filing, stage):
        from django.utils import timezone
        if filing is None:
            return
            
        FailureEvent.objects.filter(
            filing=filing,
            stage=stage,
            resolved_at__isnull=True
        ).update(resolved_at=timezone.now())

    @staticmethod
    def unresolved_for_filing(filing):
        if filing is None:
            return FailureEvent.objects.none()
            
        return FailureEvent.objects.filter(
            filing=filing,
            resolved_at__isnull=True
        ).order_by("-created_at")