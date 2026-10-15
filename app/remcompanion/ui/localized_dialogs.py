"""Local dialog adapters: translate reviewed UI copy, not paths or user data.

The native tkinter modules are not patched. Each importing surface explicitly
opts into these wrappers, with the translator taken from its dialog parent.
"""
from tkinter import filedialog as _files, messagebox as _messages, simpledialog as _simple
from .locale_controller import translator_for


class _Dialogs:
    def __init__(self, module, positional):
        self._module = module
        self._positional = positional

    def __getattr__(self, name):
        original = getattr(self._module, name)
        if not callable(original) or name[:1].isupper():
            return original

        def localized(*args, **options):
            tr = translator_for(options.get('parent'))
            args = list(args)
            for index in range(min(self._positional, len(args))):
                if isinstance(args[index], str):
                    args[index] = tr.display(args[index])
            for key in ('title', 'message', 'prompt'):
                if isinstance(options.get(key), str):
                    options[key] = tr.display(options[key])
            return original(*args, **options)

        return localized


messagebox = _Dialogs(_messages, 2)
filedialog = _Dialogs(_files, 0)
simpledialog = _Dialogs(_simple, 2)
