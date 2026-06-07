import { useState } from "react";
import { api, type Setting } from "../api";
import type { Actions } from "../App";
import { usePoll } from "../hooks";

export function Settings({ actions }: { actions: Actions }) {
  const settings = usePoll(() => api.settings(), 15000);
  const list = settings.data ?? [];
  const groups = Array.from(new Set(list.map((s) => s.group)));

  return (
    <div>
      <div className="view-head">
        <h1>Settings</h1>
      </div>
      <p className="view-sub">
        Operational limits — applied live unless marked <span className="tag">restart</span>.
        Secrets, credentials, hosts and pool sizes stay in <code>.env</code>.
      </p>
      {groups.map((g) => (
        <div className="settings-group card" key={g} style={{ padding: "4px 0 0", marginBottom: 20 }}>
          <h3 style={{ padding: "12px 16px 0" }}>{g}</h3>
          {list.filter((s) => s.group === g).map((s) => (
            <Row key={s.key} setting={s} actions={actions} onSaved={settings.refresh} />
          ))}
        </div>
      ))}
      {!list.length && !settings.loading && <div className="empty">No editable settings.</div>}
    </div>
  );
}

function Row({ setting, actions, onSaved }: { setting: Setting; actions: Actions; onSaved: () => void }) {
  const [val, setVal] = useState<string>(String(setting.value));
  const dirty = val !== String(setting.value);

  const save = async (value: string) => {
    const ok = await actions.act({ intent: "update_setting", key: setting.key, value });
    if (ok) onSaved();
  };

  return (
    <div className="setting">
      <div>
        <div className="slabel">
          {setting.label}
          {setting.restart_required && <span className="tag">restart</span>}
        </div>
        <div className="shelp">{setting.help}</div>
      </div>
      <div className="row">
        {setting.kind === "bool" ? (
          <div
            className="switch"
            data-on={String(setting.value) === "true" || setting.value === true}
            onClick={() => save(setting.value === true || String(setting.value) === "true" ? "false" : "true")}
          >
            <span className="knob" />
          </div>
        ) : setting.kind === "choice" ? (
          <select className="input" value={String(setting.value)} onChange={(e) => save(e.target.value)}>
            {setting.choices.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <>
            <input
              className="input"
              style={{ width: 150 }}
              type={setting.kind === "int" || setting.kind === "float" ? "number" : "text"}
              value={val}
              onChange={(e) => setVal(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && dirty && save(val)}
            />
            <button className="btn sm primary" disabled={!dirty} onClick={() => save(val)}>Save</button>
          </>
        )}
      </div>
    </div>
  );
}
