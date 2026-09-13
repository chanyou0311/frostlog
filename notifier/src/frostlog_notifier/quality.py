"""The message for a failed daily contract test.

A passing report is silence on purpose: the daily run pings a dead man's switch,
so a quiet channel plus a healthy switch means the data is fine.
"""

from frostlog_notifier import formatting
from frostlog_notifier.events import QualityReport
from frostlog_notifier.notification import QUALITY, Notification


def build(report: QualityReport) -> Notification | None:
    if report.passed:
        return None
    checks = "、".join(report.failed_checks) if report.failed_checks else "(名前なし)"
    text = "\n".join(
        [
            f":rotating_light: 品質チェック失敗 {report.contract_id}"
            f" ({formatting.full_stamp(report.published_at)})",
            f"失敗した検査 {len(report.failed_checks)} 件: {checks}",
            f"実行 ID: {report.run_id}",
        ]
    )
    return Notification(kind=QUALITY, text=text)
