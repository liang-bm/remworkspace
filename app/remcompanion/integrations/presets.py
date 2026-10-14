"""Reviewed public entry points; viewing a preset never connects or installs."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode
from .storage import validate_mcp

COMMANDS_DOC='https://learn.chatgpt.com/docs/app/commands'

MCP_PRESETS=(
    {'id':'context7','name':'Context7','description':'查找当前库文档和官方用法',
     'docs':'https://github.com/upstash/context7/blob/master/docs/resources/all-clients.mdx',
     'setup':'HTTPS · 在 CONTEXT7_AUTHORIZATION 中设置完整的 Bearer API Key；不复用其他软件的登录。',
     'config':{'transport':'http','url':'https://mcp.context7.com/mcp','headers_env':{'Authorization':'CONTEXT7_AUTHORIZATION'}}},
    {'id':'playwright','name':'Playwright','description':'浏览器页面、表单与截图',
     'docs':'https://github.com/microsoft/playwright-mcp',
     'setup':'Node.js 18+ / npx；连接时可能下载 npm 包。隔离浏览器，不使用日常登录配置；不自动操作网页。',
     'config':{'transport':'stdio','command':'npx','args':['-y','@playwright/mcp@latest','--isolated']}},
    {'id':'sequential-thinking','name':'Sequential Thinking','description':'分步骤整理、修正与分支推理记录',
     'docs':'https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking',
     'setup':'Node.js / npx；连接时可能下载 npm 包。保存推理步骤的工具，不是另一个模型。',
     'config':{'transport':'stdio','command':'npx','args':['-y','@modelcontextprotocol/server-sequential-thinking']}},
    {'id':'filesystem','name':'Filesystem','description':'只访问你明确选择的文件目录',
     'docs':'https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem','directory':True,
     'setup':'Node.js / npx；先选择允许的目录。包含写文件工具，每次执行仍需明确操作。',
     'config':{'transport':'stdio','command':'npx','args':['-y','@modelcontextprotocol/server-filesystem']}},
    {'id':'memory','name':'Memory','description':'本地实体与关系知识图谱',
     'docs':'https://github.com/modelcontextprotocol/servers/tree/main/src/memory',
     'setup':'Node.js / npx；独立 JSONL 数据文件，不读取或替换雷姆的长期记忆。',
     'config':{'transport':'stdio','command':'npx','args':['-y','@modelcontextprotocol/server-memory']}},
    {'id':'fetch','name':'Fetch','description':'提取公开网页内容',
     'docs':'https://github.com/modelcontextprotocol/servers/tree/main/src/fetch',
     'setup':'uv / uvx；连接时可能下载 Python 包。不关闭服务的私有地址防护，也不自动请求网页。',
     'config':{'transport':'stdio','command':'uvx','args':['mcp-server-fetch'],'env':{'PYTHONIOENCODING':'utf-8'}}},
    {'id':'git','name':'Git','description':'检查指定仓库的状态、差异与历史',
     'docs':'https://github.com/modelcontextprotocol/servers/tree/main/src/git','directory':True,
     'setup':'Git / uvx；选择仓库内目录后服务会获得整个仓库的范围。包含提交等写操作，不自动执行。',
     'config':{'transport':'stdio','command':'uvx','args':['mcp-server-git','--repository']}},
    {'id':'github','name':'GitHub · 只读','description':'仓库、Issue、PR 与工作流查询',
     'docs':'https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md',
     'setup':'HTTPS · 在 GITHUB_MCP_AUTHORIZATION 中设置完整的 Bearer PAT，仅授予需要的读取范围。',
     'config':{'transport':'http','url':'https://api.githubcopilot.com/mcp/readonly','headers_env':{'Authorization':'GITHUB_MCP_AUTHORIZATION'}}},
)

def mcp_preset(identifier,root,*,directory=None,existing=()):
    preset=next((value for value in MCP_PRESETS if value['id']==identifier),None)
    if preset is None:raise ValueError('未知 MCP 预设')
    value=deepcopy(preset['config'])
    if preset.get('directory'):
        if not directory:raise ValueError('请先选择明确允许的目录')
        path=Path(directory).resolve(strict=True)
        if not path.is_dir():raise ValueError('请选择目录')
        if path==Path(path.anchor):raise ValueError('请选具体工作目录，不允许整个磁盘或文件系统根目录')
        value['args'].append(str(path))
    if identifier=='memory':value['env']={'MEMORY_FILE_PATH':str(Path(root).resolve()/'data/integrations/mcp-memory.jsonl')}
    chosen=identifier;number=2
    while chosen in existing:chosen=f'{identifier}-{number}';number+=1
    value.update(id=chosen,name=preset['name'],description=preset['description'],enabled=False,
                 protocol_version='2025-11-25')
    return validate_mcp(value)

# Public plugin IDs were confirmed by the installed marketplace metadata.
# Built-in host tools deliberately have no invented plugin/OAuth identifier.
APPLICATIONS=(
    ('awesome-slides','Awesome Slides','幻灯片、视频与配音',None),
    ('sites','Sites','网站创建与发布',None),
    ('gmail','Gmail','邮件查找和引用','gmail'),
    ('google-drive','Google Drive','文件、文档、表格和幻灯片','google-drive'),
    ('canva','Canva','设计创建、修改和审阅','canva'),
    ('github','GitHub','仓库、Issue 和 Pull Request','github'),
    ('google-calendar','Google Calendar','日历、日程与可用时间','google-calendar'),
    ('google-contacts','Google Contacts','联系人信息','google-contacts'),
    ('pets','Pets','Codex Work 动态伙伴',None),
    ('codex-document-control','Codex Document Control','已经连接的文档会话',None),
    ('hotline','Hotline','所在地区的帮助热线',None),
    ('chatgpt-space','ChatGPT Space','页面与空间',None),
    ('plugin-management','Plugin Management','Codex 插件与连接管理',None),
)

def application_rows(saved=()):
    permissions={value['id']:value.get('enabled') is True for value in saved}
    return [{'id':identifier,'name':name,'description':description,'plugin':plugin,
             'enabled':permissions.get(identifier,False)} for identifier,name,description,plugin in APPLICATIONS]

def application_link(identifier,*,connect=False):
    row=next((value for value in application_rows() if value['id']==identifier),None)
    if row is None:raise ValueError('未知应用入口')
    if connect:
        return ('codex://plugins/install/'+row['plugin']+'?marketplace=openai-curated') if row['plugin'] else 'codex://settings'
    mention=('[@'+row['name']+'](plugin://'+row['plugin']+'@openai-curated) ') if row['plugin'] else ''
    prompt=mention+'请使用 '+row['name']+'。先列出当前可用功能；需要登录时让我完成授权，暂时不要修改或发送任何数据。'
    return 'codex://new?'+urlencode({'prompt':prompt})
