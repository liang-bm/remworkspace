"""Compatibility route for the current platform's instance activation."""
from __future__ import annotations
from .platforms import native_module

_native = native_module("activation")
EVENT_NAME = _native.EVENT_NAME
EVENT_MODIFY_STATE = _native.EVENT_MODIFY_STATE
WAIT_OBJECT_0 = _native.WAIT_OBJECT_0
signal_existing_instance = _native.signal_existing_instance
ActivationListener = _native.ActivationListener
