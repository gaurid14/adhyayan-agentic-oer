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

  try {
    const res = await fetch(`/forum/course/${courseId}/chapters/`, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!res.ok) return;
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
  } catch (err) {
    console.warn("Load chapters failed", err);
  }
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

document.addEventListener("DOMContentLoaded", () => {
  // Only inside the ASK form (inside #ask)
  const askForm = document.querySelector("#ask form");
  if (!askForm) return;

  const askCourse = askForm.querySelector('select[name="course"]');
  const askChapter = askForm.querySelector('select[name="chapter"]');
  if (!askCourse || !askChapter) return;

  // your dynamic logic ONLY here (if any)
});

// ============================
// Ask Question Draft (safe)
// - Clears draft on submit
// - Keeps a short backup in sessionStorage to restore if form returns with errors
// ============================
(function () {
  const KEY = "oer_forum_ask_draft_v1";
  const BACKUP_KEY = "oer_forum_ask_draft_backup_v1";

  const debounce = (fn, ms = 700) => {
    let t = null;
    return (...args) => {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  };

  function readLocalDraft() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null"); }
    catch { return null; }
  }

  function writeLocalDraft(payload) {
    try { localStorage.setItem(KEY, JSON.stringify(payload)); } catch {}
  }

  function clearLocalDraft() {
    try { localStorage.removeItem(KEY); } catch {}
  }

  function readBackup() {
    try { return JSON.parse(sessionStorage.getItem(BACKUP_KEY) || "null"); }
    catch { return null; }
  }

  function writeBackup(payload) {
    try { sessionStorage.setItem(BACKUP_KEY, JSON.stringify(payload)); } catch {}
  }

  function clearBackup() {
    try { sessionStorage.removeItem(BACKUP_KEY); } catch {}
  }

  function setStatus(text) {
    const el = document.getElementById("draftStatus");
    if (el) el.textContent = text || "";
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const title = document.getElementById("id_title");
    const content = document.getElementById("id_content");
    if (!title || !content) return;

    const form = title.closest("form");
    if (!form) return;

    const course = document.getElementById("id_course");
    const chapter = document.getElementById("id_chapter");
    const clearBtn = document.getElementById("clearDraftBtn");

    const hasErrors =
      document.getElementById("askFormHasErrors")?.value === "1" ||
      !!form.querySelector(".errorlist, .is-invalid");

    // 1) If we just submitted and Django returned errors,
    // restore from session backup (so nothing is lost).
    if (hasErrors) {
      const backup = readBackup();
      if (backup) {
        if (!title.value) title.value = backup.title || "";
        if (!content.value) content.value = backup.content || "";

        if (course && backup.course_id) course.value = String(backup.course_id || "");

        if (course && chapter && course.value) {
          await loadChapters(course.value, chapter, backup.chapter_id || null);
        } else if (chapter && backup.chapter_id) {
          chapter.value = String(backup.chapter_id || "");
        }

        // Keep the draft saved again (since submission failed)
        writeLocalDraft(backup);
        setStatus("Draft restored (submission had errors)");
      }
    } else {
      // If no errors, submission likely succeeded at least once:
      // cleanup backup so it doesn't come back later.
      clearBackup();
    }

    // 2) Normal restore from local draft (only if fields empty)
    const draft = readLocalDraft();
    if (draft && !hasErrors) {
      if (!title.value) title.value = draft.title || "";
      if (!content.value) content.value = draft.content || "";

      if (course && draft.course_id && !course.value) course.value = String(draft.course_id);

      if (course && chapter && course.value) {
        await loadChapters(course.value, chapter, draft.chapter_id || null);
      } else if (chapter && draft.chapter_id && !chapter.value) {
        chapter.value = String(draft.chapter_id);
      }

      setStatus("Draft restored");
    }

    const buildPayload = () => ({
      title: (title.value || ""),
      content: (content.value || ""),
      course_id: course ? (course.value || "") : "",
      chapter_id: chapter ? (chapter.value || "") : "",
      updated_at: Date.now(),
    });

    const saveNow = () => {
      const payload = buildPayload();
      const hasText = payload.title.trim() || payload.content.trim();

      if (!hasText) {
        clearLocalDraft();
        setStatus("");
        return;
      }

      writeLocalDraft(payload);
      setStatus("Draft saved");
    };

    const saveDebounced = debounce(saveNow, 700);

    title.addEventListener("input", saveDebounced);
    content.addEventListener("input", saveDebounced);
    if (course) course.addEventListener("change", saveDebounced);
    if (chapter) chapter.addEventListener("change", saveDebounced);

    // ✅ Clear draft when user submits the post
    form.addEventListener("submit", () => {
      const payload = buildPayload();

      // keep a short backup in case server returns form errors
      writeBackup(payload);

      // clear local draft immediately (what you asked)
      clearLocalDraft();
      setStatus("Submitting…");
    });

    // Clear draft button
    if (clearBtn) {
      clearBtn.addEventListener("click", async () => {
        clearLocalDraft();
        clearBackup();

        title.value = "";
        content.value = "";

        if (course) course.value = "";

        if (chapter) {
          chapter.innerHTML = `<option value="">All chapters</option>`;
          chapter.disabled = true;
        }

        setStatus("Draft cleared");
      });
    }
  });
})();