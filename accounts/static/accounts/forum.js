function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== "") {
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + "=")) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

const csrftoken = getCookie("csrftoken");

// AJAX upvote buttons
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-upvote-url]");
  if (!btn) return;

  e.preventDefault();

  const url = btn.getAttribute("data-upvote-url");
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrftoken,
        "X-Requested-With": "XMLHttpRequest",
      },
    });

    const data = await res.json();
    if (!data.ok) return;

    // update count text
    // update all matching counters (button + stat pills etc)
const key =
  btn.getAttribute("data-upvote-key") ||
  btn.querySelector("[data-upvote-key]")?.getAttribute("data-upvote-key");
if (key) {
  document.querySelectorAll(`[data-upvote-key="${key}"]`).forEach(el => {
    el.textContent = data.count;
  });
} else {
  // fallback: just update button count
  const countEl = btn.querySelector("[data-upvote-count]");
  if (countEl) countEl.textContent = data.count;
}

    // subtle state styling
    btn.classList.toggle("is-voted", data.state === "added");

  } catch (err) {
    // fail silently (no aggressive behavior)
    console.warn("Upvote failed", err);
  }
});

// Counters (optional)
function attachCounter(textareaSelector, counterSelector, maxLen) {
  const ta = document.querySelector(textareaSelector);
  const counter = document.querySelector(counterSelector);
  if (!ta || !counter) return;

  const update = () => {
    const n = (ta.value || "").length;
    counter.textContent = `${n}/${maxLen}`;
  };

  ta.addEventListener("input", update);
  update();
}

document.addEventListener("DOMContentLoaded", () => {
  attachCounter("#id_title", "#titleCounter", 255);
  attachCounter("#id_content", "#questionCounter", 10000);
});
// Submit reply on Enter (Shift+Enter = newline)
document.addEventListener("keydown", (e) => {
  const ta = e.target;
  if (!ta || ta.tagName !== "TEXTAREA") return;

  // Only for reply boxes (not the main answer box)
  if (!ta.hasAttribute("data-enter-submit")) return;

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const form = ta.closest("form");
    if (form) form.requestSubmit();
  }
});
// Auto-focus reply textarea when Reply opens
document.addEventListener("toggle", (e) => {
  const details = e.target;
  if (!(details instanceof HTMLDetailsElement)) return;
  if (!details.hasAttribute("data-reply-box")) return;

  if (details.open) {
    // Wait a tick so layout completes
    setTimeout(() => {
      const ta = details.querySelector("[data-reply-input]");
      if (ta) {
        ta.focus();
        // put cursor at end
        const v = ta.value || "";
        ta.setSelectionRange(v.length, v.length);
      }
    }, 0);
  }
}, true);

async function loadChapters(courseId, chapterSelect, selectedId = null) {
  chapterSelect.innerHTML = `<option value="">All chapters</option>`;
  if (!courseId) {
    chapterSelect.disabled = true;
    return;
  }

  chapterSelect.disabled = false;

  const res = await fetch(`/forum/course/${courseId}/chapters/`);
  const data = await res.json();
  if (!data.ok) return;

  // If it's the "ask form" dropdown, text should be "Select chapter"
  // but we keep it simple and professional.
  data.chapters.forEach(ch => {
    const opt = document.createElement("option");
    opt.value = ch.id;
    opt.textContent = `${ch.chapter_number}. ${ch.chapter_name}`;

    if (selectedId && Number(selectedId) === Number(ch.id)) opt.selected = true;
    chapterSelect.appendChild(opt);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  // Filter dropdowns
  const filterCourse = document.getElementById("filterCourse");
  const filterChapter = document.getElementById("filterChapter");

  if (filterCourse && filterChapter) {
    filterCourse.addEventListener("change", async () => {
      await loadChapters(filterCourse.value, filterChapter);
      filterCourse.closest("form")?.submit();
    });

    filterChapter.addEventListener("change", () => {
      filterChapter.closest("form")?.submit();
    });

    // OPTIONAL (nice): if page loads with selected course, populate chapters
    if (filterCourse.value) {
      await loadChapters(filterCourse.value, filterChapter, filterChapter.value);
    }
  }

  // Ask form dropdowns
  const askCourse = document.getElementById("id_course");
  const askChapter = document.getElementById("id_chapter");

  if (askCourse && askChapter) {
    askCourse.addEventListener("change", async () => {
      await loadChapters(askCourse.value, askChapter);
    });

    // on page load (important when form re-renders with errors)
    if (askCourse.value) {
      await loadChapters(askCourse.value, askChapter, askChapter.value);
    }
  }
});
