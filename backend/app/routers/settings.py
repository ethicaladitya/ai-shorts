"""Settings router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSettings
from app.config import settings
from app.services.script_generator import test_ai_connection

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")


def _get_setting(db: Session, key: str, default: str = "") -> str:
    s = db.query(AppSettings).filter(AppSettings.key == key).first()
    return s.value if s else default


def _set_setting(db: Session, key: str, value: str):
    s = db.query(AppSettings).filter(AppSettings.key == key).first()
    if s:
        s.value = value
    else:
        s = AppSettings(key=key, value=value)
        db.add(s)
    db.commit()


@router.get("")
async def settings_page(request: Request, db: Session = Depends(get_db)):
    # Load all settings from DB if present, else fallback to config
    db_settings = {
        "ai_provider": _get_setting(db, "ai_provider", settings.ai_provider),
        "voice_provider": _get_setting(db, "voice_provider", settings.voice_provider),
        "azure_openai_endpoint": _get_setting(db, "azure_openai_endpoint", settings.azure_openai_endpoint),
        "azure_openai_api_key": _get_setting(db, "azure_openai_api_key", settings.azure_openai_api_key),
        "azure_openai_deployment": _get_setting(db, "azure_openai_deployment", settings.azure_openai_deployment),
        "azure_openai_api_version": _get_setting(db, "azure_openai_api_version", settings.azure_openai_api_version),
        "azure_openai_tts_endpoint": _get_setting(db, "azure_openai_tts_endpoint", settings.azure_openai_tts_endpoint),
        "azure_openai_tts_api_key": _get_setting(db, "azure_openai_tts_api_key", settings.azure_openai_tts_api_key),
        "azure_openai_tts_api_version": _get_setting(db, "azure_openai_tts_api_version", settings.azure_openai_tts_api_version),
        "azure_openai_tts_deployment": _get_setting(db, "azure_openai_tts_deployment", settings.azure_openai_tts_deployment),
        "azure_openai_tts_voice": _get_setting(db, "azure_openai_tts_voice", settings.azure_openai_tts_voice),
        "elevenlabs_api_key": _get_setting(db, "elevenlabs_api_key", settings.elevenlabs_api_key),
        "elevenlabs_voice_id": _get_setting(db, "elevenlabs_voice_id", settings.elevenlabs_voice_id),
        "mai_voice_endpoint": _get_setting(db, "mai_voice_endpoint", settings.mai_voice_endpoint),
        "mai_voice_api_key": _get_setting(db, "mai_voice_api_key", settings.mai_voice_api_key),
        "mai_voice_name": _get_setting(db, "mai_voice_name", settings.mai_voice_name),
        "azure_speech_region": _get_setting(db, "azure_speech_region", settings.azure_speech_region),
        "azure_speech_api_key": _get_setting(db, "azure_speech_api_key", settings.azure_speech_api_key),
        "azure_speech_voice": _get_setting(db, "azure_speech_voice", settings.azure_speech_voice),
    }
    # Patch runtime settings for this request (so template shows correct values)
    for k, v in db_settings.items():
        setattr(settings, k, v)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "db_settings": db_settings,
    })


@router.post("/save")
async def save_settings(
    request: Request,
    ai_provider: str = Form("azure_openai"),
    voice_provider: str = Form("azure_openai"),
    azure_endpoint: str = Form(""),
    azure_api_key: str = Form(""),
    azure_deployment: str = Form(""),
    azure_api_version: str = Form(""),
    azure_tts_endpoint: str = Form(""),
    azure_tts_api_key: str = Form(""),
    azure_tts_api_version: str = Form(""),
    azure_tts_deployment: str = Form(""),
    azure_tts_voice: str = Form(""),
    elevenlabs_api_key: str = Form(""),
    elevenlabs_voice_id: str = Form(""),
    mai_voice_endpoint: str = Form(""),
    mai_voice_api_key: str = Form(""),
    mai_voice_name: str = Form(""),
    azure_speech_region: str = Form(""),
    azure_speech_api_key: str = Form(""),
    azure_speech_voice: str = Form(""),
    db: Session = Depends(get_db),
):
    _set_setting(db, "ai_provider", ai_provider)
    _set_setting(db, "voice_provider", voice_provider)
    if azure_endpoint:
        _set_setting(db, "azure_openai_endpoint", azure_endpoint)
    if azure_api_key:
        _set_setting(db, "azure_openai_api_key", azure_api_key)
    if azure_deployment:
        _set_setting(db, "azure_openai_deployment", azure_deployment)
    if azure_api_version:
        _set_setting(db, "azure_openai_api_version", azure_api_version)
    if azure_tts_endpoint:
        _set_setting(db, "azure_openai_tts_endpoint", azure_tts_endpoint)
    if azure_tts_api_key:
        _set_setting(db, "azure_openai_tts_api_key", azure_tts_api_key)
    if azure_tts_api_version:
        _set_setting(db, "azure_openai_tts_api_version", azure_tts_api_version)
    if azure_tts_deployment:
        _set_setting(db, "azure_openai_tts_deployment", azure_tts_deployment)
    if azure_tts_voice:
        _set_setting(db, "azure_openai_tts_voice", azure_tts_voice)
    if elevenlabs_api_key:
        _set_setting(db, "elevenlabs_api_key", elevenlabs_api_key)
    if elevenlabs_voice_id:
        _set_setting(db, "elevenlabs_voice_id", elevenlabs_voice_id)
    if mai_voice_endpoint:
        _set_setting(db, "mai_voice_endpoint", mai_voice_endpoint)
    if mai_voice_api_key:
        _set_setting(db, "mai_voice_api_key", mai_voice_api_key)
    if mai_voice_name:
        _set_setting(db, "mai_voice_name", mai_voice_name)
    if azure_speech_region:
        _set_setting(db, "azure_speech_region", azure_speech_region)
    if azure_speech_api_key:
        _set_setting(db, "azure_speech_api_key", azure_speech_api_key)
    if azure_speech_voice:
        _set_setting(db, "azure_speech_voice", azure_speech_voice)

    # Update runtime settings for current process
    settings.ai_provider = ai_provider
    settings.voice_provider = voice_provider
    if azure_endpoint:
        settings.azure_openai_endpoint = azure_endpoint
    if azure_api_key:
        settings.azure_openai_api_key = azure_api_key
    if azure_deployment:
        settings.azure_openai_deployment = azure_deployment
    if azure_api_version:
        settings.azure_openai_api_version = azure_api_version
    if azure_tts_endpoint:
        settings.azure_openai_tts_endpoint = azure_tts_endpoint
    if azure_tts_api_key:
        settings.azure_openai_tts_api_key = azure_tts_api_key
    if azure_tts_api_version:
        settings.azure_openai_tts_api_version = azure_tts_api_version
    if azure_tts_deployment:
        settings.azure_openai_tts_deployment = azure_tts_deployment
    if azure_tts_voice:
        settings.azure_openai_tts_voice = azure_tts_voice
    if elevenlabs_api_key:
        settings.elevenlabs_api_key = elevenlabs_api_key
    if elevenlabs_voice_id:
        settings.elevenlabs_voice_id = elevenlabs_voice_id
    if mai_voice_endpoint:
        settings.mai_voice_endpoint = mai_voice_endpoint
    if mai_voice_api_key:
        settings.mai_voice_api_key = mai_voice_api_key
    if mai_voice_name:
        settings.mai_voice_name = mai_voice_name
    if azure_speech_region:
        settings.azure_speech_region = azure_speech_region
    if azure_speech_api_key:
        settings.azure_speech_api_key = azure_speech_api_key
    if azure_speech_voice:
        settings.azure_speech_voice = azure_speech_voice

    return HTMLResponse(
        '<div class="bg-green-50 border border-green-200 rounded-lg p-4 text-green-700">'
        '<strong>Settings saved.</strong> Provider configuration updated.</div>'
    )


@router.post("/test-connection")
async def test_connection(request: Request):
    result = await test_ai_connection()
    if result["status"] == "ok":
        return HTMLResponse(
            f'<div class="bg-green-50 border border-green-200 rounded-lg p-4 text-green-700">'
            f'<strong>Connected!</strong> {result["message"]}</div>'
        )
    else:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">'
            f'<strong>Connection failed:</strong> {result["message"]}</div>'
        )
