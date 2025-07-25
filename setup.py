#!/usr/bin/env python

from __future__ import annotations

from setuptools import find_packages, setup

with open("README.md") as fh:
    long_description = fh.read()

dependencies = [
    "packaging",
    "pytest",
    "pytest-asyncio",
    "pytimeparse",
    "anyio",
    "chik-blockchain==2.5.4",
]

dev_dependencies = [
    "anyio",
    "mypy",
    "ruff>=0.8.1",
    "types-aiofiles",
    "types-click",
    "types-cryptography",
    "types-setuptools",
    "types-pyyaml",
    "types-setuptools",
    "pre-commit",
]

setup(
    name="chik_dev_tools",
    packages=find_packages(exclude=("tests",)),
    author="Quexington",
    entry_points={
        "console_scripts": ["cdv = cdv.cmds.cli:main"],
    },
    package_data={
        "": ["*.klvm", "*.klvm.hex", "*.clib", "*.clsp", "*.clsp.hex"],
    },
    author_email="m.hauff@chiknetwork.com",
    setup_requires=["setuptools_scm"],
    install_requires=dependencies,
    url="https://github.com/Chik-Network",
    license="https://opensource.org/licenses/Apache-2.0",
    description="Chik development commands",
    long_description=long_description,
    long_description_content_type="text/markdown",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: Apache Software License",
        "Topic :: Security :: Cryptography",
    ],
    extras_require=dict(
        dev=dev_dependencies,
    ),
    project_urls={
        "Bug Reports": "https://github.com/Chik-Network/chik-dev-tools",
        "Source": "https://github.com/Chik-Network/chik-dev-tools",
    },
)
