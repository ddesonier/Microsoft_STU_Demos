(() => {
  const storageKey = "msdocs-knowledge-chat-v1";
  const transcript = document.getElementById("chat-transcript");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-message");
  const clearButton = document.getElementById("clear-chat");
  if (!transcript || !form || !input || !clearButton) return;

  let turns = [];
  let activeRequest = null;
  let conversationGeneration = 0;
  try {
    turns = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
    if (!Array.isArray(turns)) turns = [];
  } catch {
    turns = [];
  }

  function persist() {
    turns = turns.slice(-12);
    sessionStorage.setItem(storageKey, JSON.stringify(turns));
  }

  function appendText(parent, tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    parent.appendChild(element);
    return element;
  }

  function renderTurn(turn) {
    const wrapper = document.createElement("article");
    wrapper.className = `card mb-3 shadow-sm ${turn.role === "user" ? "border-secondary" : "border-primary"}`;
    const body = document.createElement("div");
    body.className = "card-body";
    appendText(body, "div", "small fw-semibold text-body-secondary mb-2", turn.role === "user" ? "You" : "Grounded answer");
    appendText(body, "div", "answer-text", turn.content);

    if (turn.role === "assistant" && Array.isArray(turn.sources) && turn.sources.length) {
      appendText(body, "h3", "h6 mt-4", "Sources");
      const list = document.createElement("ol");
      list.className = "mb-0";
      for (const source of turn.sources) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.textContent = source.title;
        link.href = source.url || "#";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        item.appendChild(link);
        if (source.snippet) appendText(item, "div", "small text-body-secondary mt-1", source.snippet);
        list.appendChild(item);
      }
      body.appendChild(list);
    }
    wrapper.appendChild(body);
    transcript.appendChild(wrapper);
  }

  function render() {
    transcript.replaceChildren();
    if (!turns.length) {
      appendText(
        transcript,
        "div",
        "alert alert-light border text-body-secondary",
        "Ask a question to start a grounded conversation."
      );
      return;
    }
    turns.forEach(renderTurn);
    transcript.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    const history = turns.slice(-12).map(({ role, content }) => ({ role, content }));
    turns.push({ role: "user", content: message });
    persist();
    render();
    input.value = "";
    input.disabled = true;
    form.querySelector("button[type=submit]").disabled = true;
    const generation = conversationGeneration;
    activeRequest = new AbortController();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history }),
        signal: activeRequest.signal,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Chat request failed.");
      if (generation !== conversationGeneration) return;
      turns.push({
        role: "assistant",
        content: payload.answer,
        sources: payload.sources || [],
      });
    } catch (error) {
      if (error.name === "AbortError" || generation !== conversationGeneration) return;
      turns.push({ role: "assistant", content: `Error: ${error.message}`, sources: [] });
    } finally {
      activeRequest = null;
      if (generation === conversationGeneration) {
        persist();
        render();
      }
      input.disabled = false;
      form.querySelector("button[type=submit]").disabled = false;
      input.focus();
    }
  });

  clearButton.addEventListener("click", () => {
    conversationGeneration += 1;
    activeRequest?.abort();
    activeRequest = null;
    turns = [];
    sessionStorage.removeItem(storageKey);
    render();
    input.focus();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  render();
})();
