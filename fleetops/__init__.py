"""fleetops — fleet operations measurement for the Hermes kanban fleet.

Packages the improvement loop's runtime pieces: failure-cause
classification, queue stocking, and the weekly scorecard. Each piece is
pure stdlib Python, takes kanban row dicts as input, and ships with its
own test suite.
"""
