import { describe, expect, it, vi } from "vitest";
import { Draft } from "./draft";

const source = (code = "a = 1", version = "v1", edit_id: string | null = null) => ({ id: 1, code, version, edit_id });
function setup() {
  let n = 0, time = 0;
  const actions = { edit: vi.fn(), run: vi.fn(), cancel: vi.fn(), notice: vi.fn() };
  const draft = new Draft(source(), actions, () => time, () => `request-${++n}`);
  return { draft, ...actions, advance: (ms: number) => { time += ms; } };
}

describe("versioned cell drafts", () => {
  it("serializes rapid typing and bases the next edit on its own accepted predecessor", () => {
    const { draft, edit } = setup();
    draft.focused = true;
    draft.change("a = 2");
    draft.change("a = 23");
    expect(edit).toHaveBeenCalledTimes(1);
    expect(edit.mock.calls[0][0].base_version).toBe("v1");
    draft.observe(source("a = 2", "v2", "request-1"));
    expect(draft.conflict).toBe(false);
    expect(draft.code).toBe("a = 23");
    expect(edit.mock.calls[1][0]).toMatchObject({ code: "a = 23", base_version: "v2" });
    draft.observe(source("a = 23", "v3", "request-2"));
    expect(draft.busy).toBe(false);
  });

  it("offers a choice for an external edit while focused, even without local typing", () => {
    const { draft, edit } = setup();
    draft.focused = true;
    draft.observe(source("remote", "v2"));
    expect(draft.conflict).toBe(true);
    expect(draft.code).toBe("a = 1");
    draft.focused = false;
    draft.change("my revision");
    expect(edit).not.toHaveBeenCalled();
    draft.observe(source("remote again", "v3"));
    draft.keepMine();
    expect(edit).toHaveBeenLastCalledWith(expect.objectContaining({ code: "my revision", base_version: "v3" }));
  });

  it("loads the latest server text without echoing it back", () => {
    const { draft, edit, cancel } = setup();
    draft.focused = true;
    draft.observe(source("remote", "v2"));
    draft.loadServer();
    expect(draft.code).toBe("remote");
    expect(draft.conflict).toBe(false);
    expect(edit).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("updates an unfocused clean editor without a conflict", () => {
    const { draft } = setup();
    draft.observe(source("remote", "v2"));
    expect(draft.code).toBe("remote");
    expect(draft.conflict).toBe(false);
  });

  it("preserves an offline draft across snapshots and refuses to run after a stale edit", () => {
    const { draft, run, edit } = setup();
    draft.change("offline");
    draft.requestRun();
    draft.observe(source("remote", "v2"), "request-1");
    expect(draft.code).toBe("offline");
    expect(draft.conflict).toBe(true);
    expect(run).not.toHaveBeenCalled();
    draft.keepMine();
    expect(edit.mock.calls[1][0].base_version).toBe("v2");
    draft.observe(source("offline", "v3", "request-2"));
    expect(run).not.toHaveBeenCalled();
  });

  it("does not erase later typing when an earlier edit is acknowledged", () => {
    const { draft } = setup();
    draft.change("first");
    draft.change("second");
    draft.observe(source("first", "v2", "request-1"));
    expect(draft.code).toBe("second");
    draft.observe(source("first", "v2", "request-1")); // duplicate broadcast
    expect(draft.conflict).toBe(false);
  });

  it.each([4999, 5001])("expires a run waiting %d ms for pending edits", (ms) => {
    const { draft, run, advance, notice } = setup();
    draft.change("new");
    draft.requestRun();
    advance(ms);
    draft.observe(source("new", "v2", "request-1"));
    expect(run).toHaveBeenCalledTimes(ms < 5000 ? 1 : 0);
    expect(notice).toHaveBeenCalledTimes(ms > 5000 ? 1 : 0);
  });
});
