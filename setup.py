from setuptools import setup, find_packages

setup(
    name="remote-dev-control-plane",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "flask>=2.0.0",
        "requests>=2.25.0",
        "psutil>=5.8.0",
        "pyjwt>=2.0.0",
        "ansible>=4.0.0",
        "docker>=5.0.0",
        "boto3>=1.26.0",
    ],
    entry_points={
        "console_scripts": [
            "rdcp=cli.main:main",
        ],
    },
    author="Your Name",
    author_email="your.email@example.com",
    description="Control plane for remote development environments",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/remote-dev-control-plane",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)