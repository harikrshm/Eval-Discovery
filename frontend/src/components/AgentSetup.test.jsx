import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import AgentSetup from "./AgentSetup";
import { getAgentId, getToken, setAgentId, setToken } from "../lib/workspace";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PRESETS = [
  { id: "p1", name: "Alex", system_prompt: "You are Alex.", user_role_label: "Prospect", starter_messages: [] },
];

function res(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

let container;
let root;
let fetchMock;

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(AgentSetup, { onCreated: vi.fn(), ...props }));
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fetchMock = vi.fn(async (url, init = {}) => {
    if (String(url).endsWith("/presets")) return res(200, PRESETS);
    if (String(url).endsWith("/workspaces/me") && init.method === "DELETE") return res(204, null);
    if (String(url).endsWith("/agents") && init.method === "POST") {
      return res(429, { detail: "Agent limit reached for this workspace." });
    }
    return res(404, { detail: "nope" });
  });
  globalThis.fetch = fetchMock;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete globalThis.fetch;
  vi.restoreAllMocks();
});

describe("AgentSetup storage notice and delete control", () => {
  it("tells the user how long data is kept", async () => {
    await render({});
    expect(container.textContent).toContain(
      "Your prompts and chats are stored for 30 days, tied to this browser."
    );
  });

  it("hides the delete control when this browser has no workspace", async () => {
    await render({ onDeleted: vi.fn() });
    expect(buttonByText("Delete my data")).toBeUndefined();
  });

  it("confirms, deletes through the API, clears local state and resets the app", async () => {
    setToken("tok");
    setAgentId("a1");
    sessionStorage.setItem("current_transcript:a1", "[]");
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await render({ onDeleted });

    await act(async () => {
      buttonByText("Delete my data / start over").click();
    });

    const del = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(del[0]).toMatch(/\/workspaces\/me$/);
    expect(del[1].headers["X-Workspace-Token"]).toBe("tok");
    expect(getToken()).toBeNull();
    expect(getAgentId()).toBeNull();
    expect(sessionStorage.getItem("current_transcript:a1")).toBeNull();
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the user declines the confirmation", async () => {
    setToken("tok");
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await render({ onDeleted });

    await act(async () => {
      buttonByText("Delete my data / start over").click();
    });

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    expect(getToken()).toBe("tok");
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("offers the delete control inside the agent-limit error", async () => {
    setToken("tok");
    await render({ onDeleted: vi.fn() });
    await act(async () => {
      buttonByText("Alex").click();
    });
    await act(async () => {
      buttonByText("Create agent").click();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Agent limit reached for this workspace.");
    expect(alert.textContent).toContain("Delete my data / start over");
    // One control only, not a second copy in the footer section.
    expect(
      [...container.querySelectorAll("button")].filter((b) => b.textContent.includes("Delete my data"))
    ).toHaveLength(1);
  });
});
