# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

backend_dir = os.path.abspath(SPECPATH)

a = Analysis(
    ['run_frozen.py'],
    pathex=[backend_dir],
    binaries=[],
    datas=[
        (os.path.join(backend_dir, 'app'), 'app'),
        (os.path.join(backend_dir, '.env'), '.'),
    ],
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'asyncpg',
        'asyncpg.protocol',
        'httpx',
        'pydantic',
        'pydantic_core',
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'sgp4',
        'sgp4.api',
        'sgp4.model',
        'websockets',
        'aiofiles',
        'paho',
        'paho.mqtt',
        'paho.mqtt.client',
        'multipart',
        'app',
        'app.main',
        'app.config',
        'app.database',
        'app.endpoints',
        'app.urls',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy.tests'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='backend',
)
