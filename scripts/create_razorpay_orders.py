"""Creates real Razorpay test-mode orders for the subscription_failure and
checkout_abandonment records in data/batch_cases.json, writing the
resulting order_id back into each record's extra.razorpay_order_id.

Per plan.md section 8:
- checkout_abandonment: real test-mode orders, left deliberately unpaid -
  this script creates the order and nothing else ever touches it. That IS
  the abandonment. is_synthetic flips to false: nothing about these
  records is modeled anymore.
- subscription_failure: real test-mode orders + a MODELED failure event.
  Razorpay's test mode has no server-only way to force a card decline
  outside the checkout.js/browser flow, so the order is real but the
  failure itself is still synthesized using Razorpay's real error
  taxonomy - exactly what plan.md itself specifies, not a shortcut taken
  here. is_synthetic stays true; extra.data_source records which part is
  real vs. modeled.

receivable records are untouched - synthetic by design per plan.md, never
a live API call.

Safe to re-run: skips any record that already has extra.razorpay_order_id,
and before creating anything new, adopts orders left over from an
interrupted prior run by matching on receipt (=case_id) instead of
creating duplicates. Progress is saved to disk after every single record,
not just at the end, so a rate-limit or network failure partway through
never loses already-created orders.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.razorpay_client import RazorpayClient  # noqa: E402

BATCH_PATH = Path("data/batch_cases.json")


def save(records: list[dict]) -> None:
    BATCH_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        print(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set in .env - nothing to do.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    records = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    client = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)

    created = 0
    adopted = 0
    try:
        existing_by_receipt = client.list_orders_by_receipt()

        for record in records:
            if record["category"] not in ("subscription_failure", "checkout_abandonment"):
                continue

            extra = record.setdefault("extra", {})
            if extra.get("razorpay_order_id"):
                continue  # already recorded on a prior run

            case_id = record["case_id"]
            if case_id in existing_by_receipt:
                order_id = existing_by_receipt[case_id]
                adopted += 1
                print(f"{case_id}: adopting existing order {order_id} (from an earlier run)")
            else:
                order = client.create_order(
                    amount_inr=record["cart_amount_inr"],
                    receipt=case_id,
                    notes={
                        "customer_id": record["customer_id"],
                        "category": record["category"],
                        "error_code": record.get("error_code") or "",
                    },
                )
                order_id = order["id"]
                created += 1
                print(f"{case_id}: created {order_id} for INR {record['cart_amount_inr']}")
                time.sleep(1.5)  # test-mode accounts have a tight rate limit

            extra["razorpay_order_id"] = order_id
            if record["category"] == "checkout_abandonment":
                record["is_synthetic"] = False
                extra["data_source"] = "real_razorpay_test_order_left_unpaid"
            else:
                extra["data_source"] = "real_razorpay_test_order_modeled_failure_event"

            save(records)  # persist after every record - never lose progress
    finally:
        client.close()

    print(f"\nCreated {created}, adopted {adopted} real Razorpay test-mode orders.")
    print(f"{BATCH_PATH} updated in place.")


if __name__ == "__main__":
    main()
