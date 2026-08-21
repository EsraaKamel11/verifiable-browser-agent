from vba.guard.tiers import check

from .actions import Action, ActionContext


async def execute(action: Action, ctx: ActionContext, page, audit) -> None:
    """The single path to a side effect. Spec 3.1, 4.3.

    Nothing else in this codebase may call Playwright's mutating methods. If a second
    call site appears, the guard is no longer a partition.
    """
    check(action, ctx)                      # raises GuardRefusal
    element = ctx.observation.by_id(action.target_id)
    audit.action_permitted(action, element, ctx)

    sel = element.selector
    if action.kind in ("click", "submit"):
        await page.click(sel)
    elif action.kind == "fill":
        await page.fill(sel, action.value or "")
    elif action.kind == "select":
        await page.select_option(sel, action.value or "")
    elif action.kind == "hover":
        await page.hover(sel)
    elif action.kind == "navigate":
        await page.goto(action.value or "")
    elif action.kind == "scroll":
        await page.evaluate("(s) => document.querySelector(s).scrollIntoView()", sel)
    else:
        raise ValueError("unsupported action kind: " + action.kind)
