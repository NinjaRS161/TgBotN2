import aiosqlite
import os
from datetime import datetime, timedelta

from config import DATABASE_PATH, TRIAL_DAYS
from utils.crypto import decrypt_session, encrypt_session

DB = DATABASE_PATH
db_dir = os.path.dirname(DB)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    return username.lstrip("@").strip() or None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            subscription_until TEXT,
            access_granted INTEGER NOT NULL DEFAULT 0,
            balance INTEGER DEFAULT 0,
            session_string TEXT
        )
        """
        )

        # Lightweight migration for existing DBs.
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "username" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "access_granted" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN access_granted INTEGER NOT NULL DEFAULT 0")
            await db.execute(
                """
                UPDATE users
                SET access_granted = 1
                WHERE subscription_until IS NOT NULL
                """
            )
        if "trial_started_at" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN trial_started_at TEXT")
        if "trial_until" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN trial_until TEXT")
        if "trial_feedback_sent" not in columns:
            await db.execute(
                "ALTER TABLE users ADD COLUMN trial_feedback_sent INTEGER NOT NULL DEFAULT 0"
            )

        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
        """
        )

        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            text TEXT,
            delay INTEGER,
            created_at TEXT NOT NULL
        )
        """
        )

        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS mailing_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailing_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            FOREIGN KEY(mailing_id) REFERENCES mailings(id)
        )
        """
        )

        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS account_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            invited_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            UNIQUE(owner_id, invited_id)
        )
        """
        )

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mailings_telegram_id ON mailings(telegram_id)"
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mailing_recipients_mailing_username
            ON mailing_recipients(mailing_id, username)
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_account_invites_invited_id ON account_invites(invited_id)"
        )

        await db.commit()


async def save_user_profile(user_id: int, username: str | None = None):
    username = _normalize_username(username)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET username=COALESCE(excluded.username, users.username)
            """,
            (user_id, username),
        )
        await db.commit()


async def save_session(user_id: int, session: str, username: str | None = None):
    username = _normalize_username(username)
    encrypted = encrypt_session(session)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, session_string, username)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                session_string=excluded.session_string,
                username=COALESCE(excluded.username, users.username)
            """,
            (user_id, encrypted, username),
        )
        await db.commit()


async def get_session(user_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT session_string FROM users WHERE telegram_id=?", (user_id,)
        )
        row = await cursor.fetchone()
        return decrypt_session(row[0]) if row else None


async def find_user_by_username(username: str):
    username = _normalize_username(username)
    if not username:
        return None

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT telegram_id, username, session_string
            FROM users
            WHERE lower(username)=lower(?)
            """,
            (username,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "telegram_id": row[0],
            "username": row[1],
            "has_session": bool(row[2]),
        }


async def get_subscription_until(user_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT subscription_until FROM users WHERE telegram_id=?", (user_id,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            return _parse_datetime(row[0])
        return None


async def check_subscription(user_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT access_granted, subscription_until, trial_until
            FROM users
            WHERE telegram_id=?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return False

    access_granted, subscription_until, trial_until = row
    if access_granted:
        return True

    now = datetime.utcnow()
    subscription_dt = _parse_datetime(subscription_until)
    if subscription_dt and subscription_dt > now:
        return True

    trial_dt = _parse_datetime(trial_until)
    return bool(trial_dt and trial_dt > now)


async def start_trial_if_needed(user_id: int, username: str | None = None):
    username = _normalize_username(username)
    now = datetime.utcnow()
    trial_until = now + timedelta(days=max(TRIAL_DAYS, 1))

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET username=COALESCE(excluded.username, users.username)
            """,
            (user_id, username),
        )
        cursor = await db.execute(
            """
            SELECT access_granted, subscription_until, trial_started_at, trial_until
            FROM users
            WHERE telegram_id=?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            await db.commit()
            return None

        access_granted, subscription_until_raw, trial_started_raw, trial_until_raw = row
        subscription_dt = _parse_datetime(subscription_until_raw)
        if access_granted or (subscription_dt and subscription_dt > now):
            await db.commit()
            return {
                "status": "paid",
                "is_new": False,
                "trial_until": None,
            }

        if not trial_started_raw:
            await db.execute(
                """
                UPDATE users
                SET trial_started_at=?, trial_until=?, trial_feedback_sent=0
                WHERE telegram_id=?
                """,
                (now.isoformat(), trial_until.isoformat(), user_id),
            )
            await db.commit()
            return {
                "status": "trial",
                "is_new": True,
                "trial_until": trial_until,
            }

        await db.commit()
        existing_trial_until = _parse_datetime(trial_until_raw)
        return {
            "status": "trial" if existing_trial_until and existing_trial_until > now else "expired",
            "is_new": False,
            "trial_until": existing_trial_until,
        }


async def get_access_status(user_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT access_granted, subscription_until, trial_until
            FROM users
            WHERE telegram_id=?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return {"status": "none", "trial_until": None}

    access_granted, subscription_until_raw, trial_until_raw = row
    if access_granted:
        return {"status": "paid", "trial_until": None}

    now = datetime.utcnow()
    subscription_dt = _parse_datetime(subscription_until_raw)
    if subscription_dt and subscription_dt > now:
        return {"status": "paid", "trial_until": None}

    trial_until = _parse_datetime(trial_until_raw)
    if trial_until and trial_until > now:
        return {"status": "trial", "trial_until": trial_until}
    if trial_until:
        return {"status": "expired", "trial_until": trial_until}
    return {"status": "none", "trial_until": None}


async def get_expired_trials_for_feedback(limit: int = 50):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT telegram_id, username, trial_until
            FROM users
            WHERE COALESCE(access_granted, 0)=0
              AND trial_until IS NOT NULL
              AND trial_until <= ?
              AND COALESCE(trial_feedback_sent, 0)=0
            ORDER BY trial_until ASC
            LIMIT ?
            """,
            (datetime.utcnow().isoformat(), limit),
        )
        return await cursor.fetchall()


async def mark_trial_feedback_sent(user_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            UPDATE users
            SET trial_feedback_sent=1
            WHERE telegram_id=?
            """,
            (user_id,),
        )
        await db.commit()


async def extend_subscription(user_id: int, months: int = 1, username: str | None = None):
    username = _normalize_username(username)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, access_granted, username)
            VALUES (?, 1, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                access_granted=1,
                username=COALESCE(excluded.username, users.username)
            """,
            (user_id, username),
        )
        await db.commit()


async def grant_access(user_id: int, username: str | None = None):
    username = _normalize_username(username)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, access_granted, username)
            VALUES (?, 1, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                access_granted=1,
                username=COALESCE(excluded.username, users.username)
            """,
            (user_id, username),
        )
        await db.commit()


async def create_account_invite(owner_id: int, invited_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO account_invites (owner_id, invited_id, status, created_at, decided_at)
            VALUES (?, ?, 'pending', ?, NULL)
            ON CONFLICT(owner_id, invited_id)
            DO UPDATE SET
                status='pending',
                created_at=excluded.created_at,
                decided_at=NULL
            """,
            (owner_id, invited_id, datetime.utcnow().isoformat()),
        )
        cursor = await db.execute(
            """
            SELECT id
            FROM account_invites
            WHERE owner_id=? AND invited_id=?
            """,
            (owner_id, invited_id),
        )
        row = await cursor.fetchone()
        await db.commit()
        return int(row[0])


async def get_account_invite(invite_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT id, owner_id, invited_id, status
            FROM account_invites
            WHERE id=?
            """,
            (invite_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "owner_id": row[1],
            "invited_id": row[2],
            "status": row[3],
        }


async def set_account_invite_status(invite_id: int, status: str) -> bool:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            UPDATE account_invites
            SET status=?, decided_at=?
            WHERE id=? AND status='pending'
            """,
            (status, datetime.utcnow().isoformat(), invite_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_users_with_subscriptions():
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT telegram_id, username, access_granted, subscription_until
            FROM users
            ORDER BY telegram_id ASC
            """
        )
        return await cursor.fetchall()


async def create_payment(user_id: int, amount: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "INSERT INTO payments (telegram_id, amount, created_at) VALUES (?, ?, ?)",
            (user_id, amount, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_pending_payments():
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT * FROM payments WHERE status='pending'")
        return await cursor.fetchall()


async def approve_payment(payment_id: int) -> int | None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("BEGIN")
        cursor = await db.execute(
            "SELECT telegram_id, status FROM payments WHERE id=?", (payment_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await db.rollback()
            return None
        user_id, status = row
        if status != "pending":
            await db.rollback()
            return None
        await db.execute(
            """
            INSERT INTO users (telegram_id, access_granted, subscription_until)
            VALUES (?, 1, NULL)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                access_granted=1,
                subscription_until=NULL
            """,
            (user_id,),
        )
        await db.execute("UPDATE payments SET status='approved' WHERE id=?", (payment_id,))
        await db.commit()
        return user_id


async def reject_payment(payment_id: int) -> int | None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("BEGIN")
        cursor = await db.execute(
            "SELECT telegram_id, status FROM payments WHERE id=?", (payment_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await db.rollback()
            return None
        user_id, status = row
        if status != "pending":
            await db.rollback()
            return None
        await db.execute("UPDATE payments SET status='rejected' WHERE id=?", (payment_id,))
        await db.commit()
        return user_id


async def create_mailing(user_id: int, text: str, delay: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "INSERT INTO mailings (telegram_id, text, delay, created_at) VALUES (?, ?, ?, ?)",
            (user_id, text, delay, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def save_mailing_recipients(mailing_id: int, usernames: list[str]):
    normalized = []
    seen = set()
    for username in usernames:
        clean = _normalize_username(username)
        if not clean:
            continue
        lower = clean.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(clean)

    if not normalized:
        return

    async with aiosqlite.connect(DB) as db:
        await db.executemany(
            "INSERT INTO mailing_recipients (mailing_id, username) VALUES (?, ?)",
            [(mailing_id, username) for username in normalized],
        )
        await db.commit()


async def save_mailing(user_id: int, text: str, delay: int, usernames: list[str]):
    mailing_id = await create_mailing(user_id, text, delay)
    await save_mailing_recipients(mailing_id, usernames)


async def get_previously_used_usernames(user_id: int) -> set[str]:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT mr.username
            FROM mailing_recipients mr
            JOIN mailings m ON m.id = mr.mailing_id
            WHERE m.telegram_id = ?
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return {row[0].lower() for row in rows if row and row[0]}
