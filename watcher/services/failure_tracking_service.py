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