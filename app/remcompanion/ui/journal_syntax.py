"""Offline lexical colours. Code is data: it is never evaluated or imported."""
from functools import lru_cache
from pygments.token import Token
from pygments.lexers.c_cpp import CLexer, CppLexer
from pygments.lexers.python import PythonLexer
from pygments.lexers.javascript import JavascriptLexer, TypeScriptLexer
from pygments.lexers.jvm import JavaLexer
from pygments.lexers.dotnet import CSharpLexer
from pygments.lexers.go import GoLexer
from pygments.lexers.rust import RustLexer
from pygments.lexers.sql import SqlLexer
from pygments.lexers.data import JsonLexer, YamlLexer
from pygments.lexers.configs import TOMLLexer
from pygments.lexers.html import HtmlLexer
from pygments.lexers.css import CssLexer
from pygments.lexers.shell import BashLexer, PowerShellLexer
from pygments.lexers.markup import MarkdownLexer
from pygments.lexers.diff import DiffLexer

LANGUAGES = (
    ('text', '纯文本'), ('cpp', 'C++'), ('c', 'C'), ('python', 'Python'),
    ('javascript', 'JavaScript'), ('typescript', 'TypeScript'), ('java', 'Java'),
    ('csharp', 'C#'), ('go', 'Go'), ('rust', 'Rust'), ('sql', 'SQL'), ('json', 'JSON'),
    ('html', 'HTML'), ('css', 'CSS'), ('bash', 'Shell'), ('powershell', 'PowerShell'),
    ('yaml', 'YAML'), ('toml', 'TOML'), ('markdown', 'Markdown'), ('diff', 'Diff'),
)
LEXERS = dict(zip((item[0] for item in LANGUAGES[1:]), (
    CppLexer, CLexer, PythonLexer, JavascriptLexer, TypeScriptLexer, JavaLexer,
    CSharpLexer, GoLexer, RustLexer, SqlLexer, JsonLexer, HtmlLexer, CssLexer,
    BashLexer, PowerShellLexer, YamlLexer, TOMLLexer, MarkdownLexer, DiffLexer,
)))
ALIASES = {'c++': 'cpp', 'cc': 'cpp', 'cxx': 'cpp', 'py': 'python', 'py3': 'python',
           'js': 'javascript', 'ts': 'typescript', 'c#': 'csharp', 'cs': 'csharp',
           'sh': 'bash', 'shell': 'bash', 'ps1': 'powershell', 'ps': 'powershell',
           'yml': 'yaml', 'md': 'markdown', 'plain': 'text', 'plaintext': 'text', '': 'text'}
COLOURS = {
    'dark': {'comment': '#96A4B7', 'keyword': '#F3A3C8', 'type': '#E8D879',
             'string': '#B4D89B', 'number': '#C4A6F0', 'function': '#8ECEDA',
             'operator': '#8ECEDA', 'builtin': '#9CBFE9', 'inserted': '#A3D8B6',
             'deleted': '#F0A2A8'},
    'light': {'comment': '#626C7B', 'keyword': '#963B76', 'type': '#7B5B0A',
              'string': '#35622F', 'number': '#70509B', 'function': '#246875',
              'operator': '#286579', 'builtin': '#285B98', 'inserted': '#286D47',
              'deleted': '#A33D45'},
}

def language_id(value):
    value = str(value or '').strip().lower()
    return ALIASES.get(value, value)

def language_name(value):
    identifier = language_id(value)
    return dict(LANGUAGES).get(identifier, str(value or '纯文本')[:40])

def _category(token):
    if token in Token.Comment: return 'comment'
    if token in Token.Keyword.Type: return 'type'
    if token in Token.Keyword: return 'keyword'
    if token in Token.Literal.String: return 'string'
    if token in Token.Literal.Number: return 'number'
    if token in Token.Name.Function or token in Token.Name.Class: return 'function'
    if token in Token.Name.Builtin: return 'builtin'
    if token in Token.Operator: return 'operator'
    if token in Token.Generic.Inserted: return 'inserted'
    if token in Token.Generic.Deleted: return 'deleted'
    return ''

@lru_cache(maxsize=24)
def syntax_ranges(code, language):
    """Original Python-scalar offsets, without Pygments newline normalization.

    Huge pasted blocks stay editable in full; lexical work is bounded to the
    first 128 Ki characters. This cache is independent of Tk/colour/selection.
    """
    lexer = LEXERS.get(language_id(language))
    if lexer is None: return ()
    result = []
    bounded=code[:131072]
    for start, token, value in lexer().get_tokens_unprocessed(bounded+'\n'):
        category = _category(token)
        if category and value:
            end = min(len(bounded), start + len(value))
            if end <= start:continue
            if result and result[-1][0] == category and result[-1][2] == start:
                result[-1] = (category, result[-1][1], end)
            else: result.append((category, start, end))
    return tuple(result)
