"""Reusable core: storage, configuration, accounts, security, settings.

Nothing in here may import from the media domain. That rule is what makes the layer liftable into a
second project, and a test enforces it - see docs/adr/0001-split-core-from-media.md.
"""
