"""features/ — optional panel feature modules (the panel-extension seam).

Each module here defines a route contributor `(PanelContext) -> web.RouteTableDef`
and decorates it with `control_routes.@register`. A module is INERT until something
imports it; bot.py activates a feature with a single `import features.<name>` line in
its feature list (import side effect = registration). With none imported,
`control_routes.contributors()` is empty and the panel is byte-identical to core-only.

See control.py (the route seam).
"""
