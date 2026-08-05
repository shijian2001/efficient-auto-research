"""Packaging shim — do not import.

This file exists only so setuptools can ship the top-level Agent Skill suite
(``skills/arbor-*``) inside the wheel as the ``arbor.skills_suite`` package (see
``pyproject.toml``). That lets ``arbor install`` locate and copy the suite after
a plain ``pip install`` via ``arbor.cli.commands.install_cmd.bundled_skills_root``.

It carries no runtime code and is never imported by Arbor. The ``arbor install``
command copies only ``arbor-*`` skill directories, so this module is never
propagated into a target coding-agent's skills directory.
"""
