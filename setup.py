from setuptools import setup, find_packages

setup(
    name="daming-os",
    version="1.5.0",
    description="A decoupled, standalone Daming OS containing 大明记忆系统 3.0 and 大明成长系统 2.0",
    author="Wenchen Ma",
    packages=find_packages(),
    install_requires=[],
    extras_require={
        "vector": ["lancedb>=0.6.0"],
        "llm": ["litellm>=1.0.0"],
        "webhook": ["requests>=2.0.0"],
        "tokens": ["tiktoken>=0.5.0"],
        "full": ["lancedb>=0.6.0", "litellm>=1.0.0", "requests>=2.0.0", "tiktoken>=0.5.0"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "console_scripts": [
            "daming-os=daming_os.cli:main",
        ],
    },
    python_requires=">=3.9",
)
