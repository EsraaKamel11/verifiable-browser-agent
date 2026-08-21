from dataclasses import dataclass, field
from typing import Any


@dataclass
class Deps:
    """Everything one entity's run needs, built once per page by the CLI.

    It is a bag of collaborators, not a service locator: nothing here decides
    anything. The oracle is present as a field and absent from every tool list,
    which is the whole point of spec 4.3.
    """

    page: Any
    audit: Any
    vault: Any
    scrubber: Any
    store: Any
    oracle: Any
    ctx_holder: Any
    grant: Any
    contract_name: str = ""
    # The entity this run is about, set by run_step from the invocation's bindings.
    # It is carried here rather than added to run_resolution's signature so the
    # canonical interface (ruling R18) is unchanged; the resolution prompt reads it
    # so a session is told which provider and which payer it is working on.
    bindings: dict = field(default_factory=dict)
    memory_enabled: bool = True
    memory_writes_enabled: bool = True
    last_http_status: int | None = None
    halt_run: bool = False
    # The final observation of the step just driven. run_step reads the page
    # confirmation out of it (ruling R7) and the refusal text out of it (R8);
    # drive() is the only writer.
    last_observation: Any = None
    # The stated refusal from the previous attempt at this step, or None. Ruling
    # R8: a resolution that is not told why the last attempt was refused will
    # repeat it.
    failure_context: str | None = None
    _epoch: int = 0
    _page_confirmation: str | None = None
    _response_listener_attached: bool = False

    def next_epoch(self) -> int:
        self._epoch += 1
        return self._epoch

    def page_confirmation(self) -> str | None:
        """The confirmation number shown on the page, or None. Read from the last
        observation by the caller and stashed here; it is one of the three
        agreements CONFIRMED requires (spec 5.3)."""
        return self._page_confirmation

    def set_page_confirmation(self, value: str | None) -> None:
        """Always called, including with None. A confirmation left over from an
        earlier step would let adjudicate agree with a page that showed nothing."""
        self._page_confirmation = value

    async def settle(self) -> None:
        await self.page.wait_for_load_state("networkidle")

    def attach_response_listener(self) -> None:
        """Ruling R13. last_http_status decides page_verify's infrastructural
        branch, and a field nobody writes would report every 5xx outage as a
        mechanical failure and route it into a resolution spiral (spec 5.2).

        Attached once per page rather than once per step, so a multi-step run does
        not stack duplicate handlers on one page.
        """
        if self._response_listener_attached:
            return
        self._response_listener_attached = True

        def _on_response(response):
            # Only the main frame's own document response describes the page the
            # agent is looking at. An image or an XHR 500 is not a portal outage.
            try:
                if response.request.resource_type != "document":
                    return
                if response.frame is not self.page.main_frame:
                    return
                self.last_http_status = response.status
            except Exception:
                # A listener is observability, never control flow. It must not be
                # able to abort an action by raising out of Playwright's dispatch.
                pass

        try:
            self.page.on("response", _on_response)
        except Exception:
            pass
