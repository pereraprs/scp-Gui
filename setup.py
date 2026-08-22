from setuptools import setup, find_packages

setup(
    name="scp-gui",
    version="0.1.0",
    description="A simple GUI SCP/SFTP file transfer client for Debian-based Linux",
    packages=find_packages(),
    install_requires=[
        "PyQt5>=5.15",
        "paramiko>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "scp-gui=scpgui.main:main",
        ],
    },
    python_requires=">=3.8",
)
