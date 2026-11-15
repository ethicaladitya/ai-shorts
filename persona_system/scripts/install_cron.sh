#!/bin/bash
# persona_system/scripts/install_cron.sh
# Installs cron jobs for the persona system.
# Run once: bash persona_system/scripts/install_cron.sh

PROJ=/Users/adityashah/dev/Personal/ai-shorts
PYTHON=$PROJ/persona_system/.venv/bin/python
LOG=$PROJ/persona_system/data/cron.log

# Write crontab
(crontab -l 2>/dev/null; cat <<EOF

# ── Persona System Cron Jobs ─────────────────────────────────────

# Check & publish due posts every 5 min
*/5 * * * * cd $PROJ && $PYTHON -m persona_system.automation.publisher >> $LOG 2>&1

# Full content generation pipeline — daily at 3am
0 3 * * * cd $PROJ && $PYTHON -m persona_system.scripts.run_pipeline --persona default --video-type loop >> $LOG 2>&1

# Fetch analytics for recent posts — every 6 hours
0 */6 * * * cd $PROJ && $PYTHON -m persona_system.scripts.fetch_analytics >> $LOG 2>&1

# ── End Persona System ───────────────────────────────────────────
EOF
) | crontab -

echo "Cron jobs installed. Check with: crontab -l"
