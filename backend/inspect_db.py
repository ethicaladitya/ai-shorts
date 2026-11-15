import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, "/Users/adityashah/dev/Personal/ai-shorts/backend")

from app.database import SessionLocal
from app.models import UGCJob

db = SessionLocal()
try:
    jobs = db.query(UGCJob).order_by(UGCJob.id.desc()).limit(10).all()
    print("Recent UGC Jobs in database:")
    for job in jobs:
        print(f"ID: {job.id} | Persona: {job.persona_name} | Status: {job.status} | Step: {job.step}")
        print(f"  Script: {job.script_raw[:50] if job.script_raw else 'None'}")
        print(f"  Images: {job.scene_images[:50] if job.scene_images else 'None'}")
        print(f"  Voice: {job.voice_file}")
        print(f"  Output: {job.output_file}")
        print(f"  Error: {job.error_message}")
        print("-" * 40)
finally:
    db.close()
