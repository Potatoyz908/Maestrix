#!/usr/bin/env python3
"""
Setup script for Maestrix GTK 4.0 UI
"""

from setuptools import setup, find_packages

setup(
    name="maestrix-gtk4",
    version="0.1.0",
    description="GTK 4.0 user interface for Maestrix",
    author="Euller",
    python_requires=">=3.9",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "PyGObject>=3.48.0",  # For GTK 4.0 bindings
    ],
    entry_points={
        "console_scripts": [
            "maestrix-gtk4=ui.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: X11 Applications :: GTK",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.9+",
        "Topic :: Software Development :: Build Tools",
    ],
)
