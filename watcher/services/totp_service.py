"""
MFA-01: TOTP (authenticator app) second factor.

Opt-in per user. A user with no CONFIRMED device authenticates exactly
as before, which is what lets this ship without a migration window.

Design notes, all of them things that are easy to get wrong:

- REPLAY. A code is valid for its whole 30-second step, so one observed
  in transit can be re-sent. `last_used_step` records the step a code
  was accepted at; anything at or below it is refused.

- DRIFT. One step either side (+/- 30s) is allowed. Wider windows
  meaningfully weaken the factor.

- UNCONFIRMED DEVICES. The secret is generated at setup but the device
  does nothing until the user submits a valid code. A failed enrolment
  must never lock the account out.

- LOCKOUT. Backup codes exist because a lost phone would otherwise be
  permanent. They are single-use and stored hashed.

- TIMING. Backup codes are compared with Django's password hasher,
  which is constant-time. Never a plain `==`.
"""

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

import pyotp

from watcher.models import BackupCode, TOTPDevice

ISSUER = "SEC Watcher"

# Steps of drift allowed either side of now.
DRIFT_STEPS = 1

BACKUP_CODE_COUNT = 10
BACKUP_CODE_BYTES = 5  # 10 hex characters


def has_confirmed_device(user):
    """True when this user must present a second factor."""
    return TOTPDevice.objects.filter(
        user=user,
        confirmed=True,
    ).exists()


def start_enrolment(user):
    """
    Create or reset an UNCONFIRMED device and return its provisioning
    URI for the QR code.

    Re-running this before confirming issues a fresh secret, so a user
    who abandoned setup half way is not stuck with a secret their app
    never received. It refuses to touch an already-confirmed device.
    """
    device = TOTPDevice.objects.filter(user=user).first()

    if device and device.confirmed:
        raise ValueError("Two-factor authentication is already enabled.")

    secret = pyotp.random_base32()

    if device:
        device.secret = secret
        device.last_used_step = None
        device.save(update_fields=["secret", "last_used_step"])
    else:
        device = TOTPDevice.objects.create(user=user, secret=secret)

    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user.get_username(),
        issuer_name=ISSUER,
    )

    return device, uri


def _current_step(for_time=None):
    return int((for_time or timezone.now()).timestamp()) // 30


def verify_code(user, code):
    """
    Check a TOTP code against the user's CONFIRMED device.

    Returns True and records the step on success. Returns False for a
    wrong code, a replayed code, or no confirmed device.
    """
    device = TOTPDevice.objects.filter(
        user=user,
        confirmed=True,
    ).first()

    if device is None:
        return False

    return _verify_against(device, code)


def _verify_against(device, code):
    code = (code or "").strip().replace(" ", "")

    if not code.isdigit():
        return False

    totp = pyotp.TOTP(device.secret)

    if not totp.verify(code, valid_window=DRIFT_STEPS):
        return False

    # Replay: find which step matched, and refuse anything already used.
    now_step = _current_step()

    matched_step = None

    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        candidate = now_step + offset

        if totp.at(candidate * 30) == code:
            matched_step = candidate
            break

    if matched_step is None:
        return False

    if (
        device.last_used_step is not None
        and matched_step <= device.last_used_step
    ):
        return False

    device.last_used_step = matched_step
    device.save(update_fields=["last_used_step"])

    return True


@transaction.atomic
def confirm_enrolment(user, code):
    """
    Turn an unconfirmed device on, once the user proves they scanned it.

    Returns the plaintext backup codes. They are shown once and never
    recoverable afterwards.
    """
    device = TOTPDevice.objects.select_for_update().filter(
        user=user,
    ).first()

    if device is None:
        raise ValueError("Start setup before confirming.")

    if device.confirmed:
        raise ValueError("Two-factor authentication is already enabled.")

    if not _verify_against(device, code):
        raise ValueError("That code is not valid. Check the time on your phone.")

    device.confirmed = True
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed", "confirmed_at"])

    return regenerate_backup_codes(user)


@transaction.atomic
def regenerate_backup_codes(user):
    """Replace every backup code. Returns the new plaintext codes."""
    BackupCode.objects.filter(user=user).delete()

    codes = [
        secrets.token_hex(BACKUP_CODE_BYTES)
        for _ in range(BACKUP_CODE_COUNT)
    ]

    BackupCode.objects.bulk_create([
        BackupCode(user=user, code_hash=make_password(code))
        for code in codes
    ])

    return codes


@transaction.atomic
def consume_backup_code(user, code):
    """
    Spend one backup code. Single use.

    Compared with Django's password hasher, which is constant-time.
    """
    code = (code or "").strip().replace(" ", "").lower()

    if not code:
        return False

    unused = BackupCode.objects.select_for_update().filter(
        user=user,
        used_at__isnull=True,
    )

    for backup in unused:
        if check_password(code, backup.code_hash):
            backup.used_at = timezone.now()
            backup.save(update_fields=["used_at"])
            return True

    return False


def unused_backup_code_count(user):
    return BackupCode.objects.filter(
        user=user,
        used_at__isnull=True,
    ).count()


@transaction.atomic
def disable(user):
    """
    Turn two-factor off and destroy the secret and every backup code.

    Callers MUST re-authenticate the user first: a stolen session must
    not be able to switch this off.
    """
    TOTPDevice.objects.filter(user=user).delete()
    BackupCode.objects.filter(user=user).delete()