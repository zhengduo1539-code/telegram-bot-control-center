"""Catch missing/renamed imports across the production application."""
import importlib


def test_runtime_modules_import_without_external_services():
    modules = (
        "config",
        "database",
        "scheduler",
        "main",
        "handlers.base",
        "handlers.chat_cleanup",
        "handlers.permissions",
        "handlers.owner_panel",
        "handlers.queue_manager",
        "handlers.schedule_wizard",
        "handlers.templates",
        "keep_alive",
    )
    for module in modules:
        assert importlib.import_module(module) is not None


def test_application_builder_registers_all_handlers():
    from main import build_application

    application = build_application()
    assert application.updater is not None
    assert application.handlers
