import { useCallback, useEffect, useRef, useState } from "react";
import { Project, ProjectItem, appendSection, getProject, getProjects, researchStatus, startResearch, toggleStep } from "../api";
import { ErrorView, errorText } from "../components/Status";
import { alertUser, haptic } from "../telegram";

type Load<T> = { kind: "loading" } | { kind: "ready"; data: T } | { kind: "error"; message: string; expired: boolean };

export function ProjectsScreen() {
  const [open, setOpen] = useState<string | null>(null);
  return open ? <ProjectCard projectKey={open} onBack={() => setOpen(null)} /> : <ProjectList onOpen={setOpen} />;
}

function ProjectList({ onOpen }: { onOpen: (key: string) => void }) {
  const [state, setState] = useState<Load<ProjectItem[]>>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: (await getProjects()).items });
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  return (
    <>
      <header>
        <h1>Проекты · {state.data.length}</h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>
      {state.data.length === 0 && <p className="center">Проектов нет. Напиши боту идею — разберём её вопросами.</p>}
      <ul>
        {state.data.map((p) => (
          <li key={p.key} className="card clickable" onClick={() => onOpen(p.key)}>
            <div className="title">{p.title}</div>
            {p.summary && <div className="note">{p.summary}</div>}
          </li>
        ))}
      </ul>
    </>
  );
}

const POLL_MS = 3000;

function ProjectCard({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const [state, setState] = useState<Load<Project>>({ kind: "loading" });
  const [editing, setEditing] = useState<{ section: string; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [researching, setResearching] = useState(false);
  const poll = useRef<number | null>(null);

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setState({ kind: "loading" });
      try {
        const data = await getProject(projectKey);
        setState({ kind: "ready", data });
        setResearching(data.research.status === "running");
      } catch (e) {
        setState({ kind: "error", ...errorText(e) });
      }
    },
    [projectKey],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Разведка идёт на сервере до минуты — опрашиваем статус, пока не закончится.
  useEffect(() => {
    if (!researching) return;
    poll.current = window.setInterval(async () => {
      try {
        const job = await researchStatus(projectKey);
        if (job.status === "running") return;
        setResearching(false);
        if (job.status === "error") alertUser(`Разведка не удалась: ${job.message ?? ""}`);
        else haptic("success");
        await load(true);
      } catch {
        // сеть мигнула — попробуем на следующем тике
      }
    }, POLL_MS);
    return () => {
      if (poll.current) window.clearInterval(poll.current);
    };
  }, [researching, projectKey, load]);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const project = state.data;

  const onToggle = async (id: string, checked: boolean) => {
    const before = project;
    setState({ kind: "ready", data: { ...project, steps: project.steps.map((s) => (s.id === id ? { ...s, checked } : s)) } });
    try {
      await toggleStep(projectKey, id, checked);
      haptic("success");
    } catch (e) {
      haptic("error");
      setState({ kind: "ready", data: before });
      alertUser((e as Error).message);
    }
  };

  const onSave = async () => {
    if (!editing || !editing.text.trim() || saving) return;
    setSaving(true);
    try {
      await appendSection(projectKey, editing.section, editing.text.trim());
      haptic("success");
      setEditing(null);
      await load(true);
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onResearch = async () => {
    if (researching) return;
    try {
      const job = await startResearch(projectKey);
      setResearching(job.status === "running");
    } catch (e) {
      alertUser((e as Error).message);
    }
  };

  const done = project.steps.filter((s) => s.checked).length;

  return (
    <>
      <button className="ghost-text back" onClick={onBack}>
        ← Проекты
      </button>
      <h1>{project.title}</h1>

      {!project.body_valid && (
        <p className="warning">Заметку проекта правили руками — дополнять отсюда нельзя, иначе правки потеряются. Дополни её в MS To Do.</p>
      )}

      {project.steps.length > 0 && (
        <section>
          <div className="section-head">
            <h2>
              Шаги · {done}/{project.steps.length}
            </h2>
          </div>
          <ul className="steps">
            {project.steps.map((s) => (
              <li key={s.id}>
                <label>
                  <input type="checkbox" checked={s.checked} onChange={(e) => void onToggle(s.id, e.target.checked)} />
                  <span className={s.checked ? "done" : undefined}>{s.text}</span>
                </label>
              </li>
            ))}
          </ul>
        </section>
      )}

      {project.sections.map((sec) => (
        <section key={sec.key} className="card">
          <div className="section-head">
            <h2>{sec.title}</h2>
            {project.body_valid && editing?.section !== sec.key && (
              <button className="ghost-text" onClick={() => setEditing({ section: sec.key, text: "" })}>
                Дополнить
              </button>
            )}
          </div>
          <div className={sec.value ? "brief" : "brief empty"}>{sec.value || "не раскрыто"}</div>
          {editing?.section === sec.key && (
            <div className="editor">
              <textarea
                rows={3}
                value={editing.text}
                placeholder="Что дописать"
                onChange={(e) => setEditing({ section: sec.key, text: e.target.value })}
              />
              <div className="actions">
                <button disabled={!editing.text.trim() || saving} onClick={() => void onSave()}>
                  Сохранить
                </button>
                <button className="ghost-text" onClick={() => setEditing(null)}>
                  Отмена
                </button>
              </div>
            </div>
          )}
        </section>
      ))}

      {project.research_enabled && (
        <button className="wide" disabled={researching} onClick={() => void onResearch()}>
          {researching ? "Ищу в интернете… до минуты" : "🔎 Разведать"}
        </button>
      )}
    </>
  );
}
