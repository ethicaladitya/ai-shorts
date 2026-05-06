"""Approval checkpoint — pauses the pipeline for script/image review before full render.

Modes:
  cli     — interactive terminal prompt (default)
  auto    — always approve (for automated runs)
  webhook — polls DB for approval decision set via HTTP endpoint
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)

ApprovalDecision = Literal["approve", "regenerate_script", "regenerate_image"]


async def run_checkpoint(
    job_id: int,
    script_formatted: str,
    first_image: Path | None,
    cost_estimate: float,
    providers: dict[str, str],
) -> ApprovalDecision:
    """Present the approval checkpoint and return the user's decision."""
    mode = settings.ugc_approval_mode

    if mode == "auto":
        logger.info("UGC checkpoint auto-approved (UGC_APPROVAL_MODE=auto)")
        return "approve"

    if mode == "webhook":
        return await _webhook_checkpoint(job_id)

    # Default: CLI
    return await _cli_checkpoint(script_formatted, first_image, cost_estimate, providers)


async def _cli_checkpoint(
    script_formatted: str,
    first_image: Path | None,
    cost_estimate: float,
    providers: dict[str, str],
) -> ApprovalDecision:
    print("\n" + "=" * 60)
    print("  UGC VIDEO ENGINE — APPROVAL CHECKPOINT")
    print("=" * 60)

    print("\n── GENERATED SCRIPT ─────────────────────────────────────")
    print(script_formatted or "(no script)")

    if first_image and first_image.exists():
        print(f"\n── FIRST SCENE IMAGE ─────────────────────────────────────")
        print(f"  {first_image}")
        # Try to open in Preview on macOS
        try:
            proc = await asyncio.create_subprocess_exec(
                "open", str(first_image),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception:
            pass

    print("\n── COST ESTIMATE ─────────────────────────────────────────")
    for stage, name in providers.items():
        from app.services.ugc.config.defaults import LOCAL_PROVIDERS, PROVIDER_COST_ESTIMATES
        cost = PROVIDER_COST_ESTIMATES.get(name, 0.0)
        label = "FREE (local)" if name in LOCAL_PROVIDERS else f"~${cost:.2f}"
        print(f"  {stage:<10} {name:<20} {label}")
    total_label = "FREE" if cost_estimate == 0.0 else f"~${cost_estimate:.2f}"
    print(f"\n  Total estimated cost: {total_label}")

    print("\n── DECISION ──────────────────────────────────────────────")
    print("  [A] Approve — proceed to full render")
    print("  [S] Regenerate script")
    print("  [I] Regenerate first scene image")
    print()

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: input("  Your choice (A/S/I): ").strip().upper())

    mapping: dict[str, ApprovalDecision] = {
        "A": "approve",
        "S": "regenerate_script",
        "I": "regenerate_image",
    }
    decision = mapping.get(raw, "approve")
    print(f"  → {decision}")
    print("=" * 60 + "\n")
    return decision


async def _webhook_checkpoint(job_id: int) -> ApprovalDecision:
    """Poll the DB until approval_decision is set by the web endpoint (non-CLI environments)."""
    logger.info("Waiting for web approval for UGC job %d (UGC_APPROVAL_MODE=webhook)", job_id)

    for _ in range(720):  # up to 1 hour
        await asyncio.sleep(5)
        try:
            from app.database import SessionLocal
            from app.models import UGCJob
            db = SessionLocal()
            try:
                job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
                if job and job.approval_decision:
                    decision = job.approval_decision
                    logger.info("Job %d approval received: %s", job_id, decision)
                    return decision  # type: ignore[return-value]
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Checkpoint poll error for job %d: %s", job_id, exc)

    logger.warning("Checkpoint timed out for job %d — auto-approving", job_id)
    return "approve"
