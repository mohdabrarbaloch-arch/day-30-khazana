"""Seed a demo account with a small, realistic knowledge base.

Run with:  python -m scripts.seed

The documents are written to be genuinely answerable — each one contains
specific facts that the demo questions ask about, so the first thing a new
user sees is the tool working rather than an empty state.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Collection, User
from app.security import hash_password
from app.services import ingest_document

DEMO_EMAIL = "abraz@example.com"
DEMO_PASSWORD = "khazana-demo-2026"

DOCUMENTS: list[tuple[str, str, str]] = [
    (
        "Refund & Returns Policy",
        "refund-policy",
        """
Refunds are issued within 30 days of purchase, provided the item is unused and
in its original packaging. To start a refund, contact support with your order
number and the email address used at checkout. Refunds are processed to the
original payment method and typically appear within five to seven business
days.

Enterprise customers on an annual contract receive a dedicated account manager
and a 60-day refund window instead of 30. Enterprise refunds above 500,000
rupees require written approval from the finance director before processing.

Digital products and gift cards are non-refundable once the licence key has
been issued. If a digital product fails to activate, support will issue a
replacement key rather than a refund.

Shipping charges are refunded only when the return is due to a defect or an
error on our side. Return shipping for change-of-mind returns is paid by the
customer.
        """.strip(),
    ),
    (
        "Expense & Approval Policy",
        "expense-policy",
        """
All business expenses must be submitted within 30 days of the transaction date.
Receipts are mandatory for any expense above 1,000 rupees; below that, a
description in the expense tool is sufficient.

Expenses up to 25,000 rupees may be approved by a direct line manager. Expenses
between 25,000 and 50,000 rupees require approval from the department head.
Any expense over 50,000 rupees requires director approval, and anything above
200,000 rupees additionally requires sign-off from the finance committee.

Travel booked more than 14 days in advance must use the corporate travel portal.
Last-minute travel within 14 days may be booked directly and expensed, but the
reason for the short notice must be recorded in the expense note.

Client entertainment is capped at 15,000 rupees per person per event. Alcohol
is not reimbursable under any circumstances.
        """.strip(),
    ),
    (
        "Onboarding Guide for New Engineers",
        "onboarding",
        """
Onboarding takes two weeks for new engineering hires. The first week is
orientation: accounts and access provisioning, a codebase walkthrough, and
pairing with a buddy on a small starter task. The second week is a supervised
contribution, ending with the new engineer shipping one small change to
production.

Every new engineer is assigned a buddy for their first 60 days. The buddy is
responsible for daily check-ins during week one and weekly check-ins after
that. Buddies are volunteers and receive a 20,000 rupee stipend per onboarding.

Development environment setup is documented in the engineering handbook. The
standard stack is Python 3.11, PostgreSQL 16, and Docker. Setup should take
under half a day; if it takes longer, that is a bug in the documentation and
should be reported to the platform team.

Access to production systems is granted after the first successful code review,
not on day one. Production access requires completing the security training
module, which takes about 90 minutes.
        """.strip(),
    ),
    (
        "Support Hours & Escalation",
        "support-hours",
        """
Standard support hours are 9am to 6pm, Monday through Friday, Pakistan Standard
Time. Support is closed on public holidays. Outside these hours, only
severity-one incidents are handled, through the on-call rotation.

Severity one means the service is completely unavailable or customer data is at
risk. The on-call engineer must acknowledge a severity-one page within 15
minutes. Severity two means significant degradation with a workaround
available, and is handled during business hours with a four-hour response
target.

The support team can be reached at support@example.com or through the in-app
chat. Phone support is available only to enterprise customers on an annual
contract.

Escalation to engineering happens when a ticket has been open for more than
three business days without resolution, or immediately for any severity-one
incident.
        """.strip(),
    ),
]


def main() -> int:
    init_db()

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                display_name="ABraz Baloch",
                password_hash=hash_password(DEMO_PASSWORD),
                is_admin=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Created demo user {DEMO_EMAIL}")
        else:
            print(f"Demo user {DEMO_EMAIL} already exists")

        collection = db.scalar(
            select(Collection).where(
                Collection.owner_id == user.id, Collection.name == "Company Handbook"
            )
        )
        if collection is None:
            collection = Collection(
                owner_id=user.id,
                name="Company Handbook",
                description="Policies, onboarding and support documentation",
            )
            db.add(collection)
            db.commit()
            db.refresh(collection)
            print("Created collection 'Company Handbook'")
        else:
            print("Collection already exists — adding any missing documents")

        existing_titles = {d.title for d in collection.documents}
        added = 0
        for title, _slug, text in DOCUMENTS:
            if title in existing_titles:
                continue
            document = ingest_document(db, collection, title, text, "text")
            added += 1
            print(f"  + {title} ({document.chunk_count} chunks)")

        print(f"\nDone. {added} document(s) added.")
        print(f"Sign in with {DEMO_EMAIL} / {DEMO_PASSWORD}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
