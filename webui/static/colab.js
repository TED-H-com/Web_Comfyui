/* Optional installed Colab lane. No Google tokens or media are stored in browser storage. */
(() => {
  "use strict";
  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
    }
    node.append(...children);
    return node;
  };
  const button = (label, action, primary = false) => el("button", {type: "button", class: `${primary ? "primary" : "ghost"} small`, text: label, onclick: action});
  const field = (title, control) => {
    control.setAttribute("aria-label", title);
    return el("label", {class: "field"}, [el("span", {text: title}), control]);
  };
  const select = (values) => el("select", {}, values.map(([value, text]) => el("option", {value, text})));
  const number = (value, min, max, step = 1) => el("input", {type: "number", value, min, max, step, required: true});
  const fileData = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name: file.name, data: String(reader.result).split(",")[1]});
    reader.onerror = () => reject(new Error("無法讀取選取的參考檔案"));
    reader.readAsDataURL(file);
  });

  async function open(opener, {api, openSheet}) {
    const root = el("div", {class: "colab-content"});
    const notice = el("p", {class: "muted colab-notice", role: "status", "aria-live": "polite", text: "正在讀取帳號與任務…"});
    root.append(notice);
    openSheet({id: "colab-workspace", title: "Google Colab · H3", opener, content: root, className: "colab-sheet"});
    let snapshot, selectedId = "", requestId = crypto.randomUUID(), lastRequest = null;
    const post = (path, body = {}) => api("/api/colab/" + path, {method: "POST", body: JSON.stringify(body)});
    const run = async (action) => {
      notice.textContent = "處理中…";
      try { await action(); notice.textContent = ""; }
      catch (error) { notice.textContent = error.message || "操作未完成，請稍後再試"; }
    };
    try { snapshot = await api("/api/colab"); }
    catch (error) { notice.textContent = error.message; return; }
    if (!snapshot.installed) {
      notice.textContent = "尚未安裝 Colab 選用環境。完成安裝設定後，即可連接 Google 帳號並在這裡生成。";
      root.append(el("a", {href: "https://github.com/TED-H-com/Web_Comfyui/blob/main/docs/colab.md", target: "_blank", rel: "noopener noreferrer", text: "查看 Colab 安裝說明"}));
    } else notice.textContent = "";

    const account = select([]), balance = el("span", {class: "muted"});
    const accounts = el("section", {class: "colab-section"});
    const accountSettings = el("details", {class: "colab-account-settings"});
    accountSettings.append(el("summary", {text: "連接或新增 Google 帳號"}));
    const label = el("input", {type: "text", placeholder: "例如：我的 Ultra、我的 Pro", maxlength: "64"});
    let flow = null;
    const authorization = el("div", {class: "colab-authorization"});
    const code = el("input", {type: "password", autocomplete: "off", spellcheck: "false", placeholder: "貼上 Google 顯示的授權碼"});
    const finish = button("完成連線", () => run(async () => {
      const pendingCode = code.value; code.value = "";
      await post(`accounts/${flow.accountId}/finish`, {flow_id: flow.flowId, code: pendingCode});
      await post(`accounts/${flow.accountId}/refresh`);
      authorization.replaceChildren(); flow = null; await refresh();
    }));
    async function login(id) {
      const result = await post(`accounts/${id}/login`);
      const url = new URL(result.authorization_url);
      if (url.protocol !== "https:" || url.hostname !== "accounts.google.com") throw new Error("Google 登入網址無效");
      flow = {accountId: id, flowId: result.flow_id};
      authorization.replaceChildren(
        el("a", {href: url.href, target: "_blank", rel: "noopener noreferrer", text: "開啟 Google 授權頁面"}),
        field("授權碼", code), finish,
      );
      code.value = "";
    }
    const create = button("新增並連接", () => run(async () => {
      const result = await post("accounts", {label: label.value}); selectedId = result.id;
      label.value = ""; await refresh(); await login(result.id);
    }));
    const reconnect = button("重新連接", () => run(() => login(account.value)));
    const refreshButton = button("更新餘額", () => run(async () => {
      await post(`accounts/${account.value}/refresh`); await refresh();
    }));
    accountSettings.append(el("div", {class: "colab-row"}, [field("帳號名稱", label), create, reconnect]), authorization);
    accounts.append(el("div", {class: "colab-row"}, [field("執行帳號", account), balance, refreshButton]), accountSettings);
    root.append(accounts);

    const form = el("form", {class: "colab-form"});
    const kind = select([["image", "生圖／圖片編輯"], ["video", "影片與聲音"], ["tts", "語音"]]);
    const mode = select([["text", "文字生影片"], ["ref", "參考素材生影片"], ["frames", "首尾幀生影片"], ["extend", "延長影片"]]);
    const prompt = el("textarea", {rows: 3, required: true, maxlength: 12000, placeholder: "描述畫面、動作或輸入要朗讀的文字"});
    const gpu = select([["L4", "L4"], ["G4", "G4"], ["A100", "A100"], ["H100", "H100"]]);
    const aspect = select([["16:9", "橫式 16:9"], ["9:16", "直式 9:16"], ["1:1", "正方形 1:1"], ["4:3", "橫式 4:3"], ["3:4", "直式 3:4"]]);
    const resolution = select([["480P", "480P"], ["720P", "720P"], ["1080P", "1080P"]]);
    const size = select([["1K", "1K"], ["1.3K", "1.3K"], ["1.6K", "1.6K"], ["2K", "2K"], ["2.7K", "2.7K"]]);
    const seconds = number(5, 2, 15), minutes = number(60, 5, 180), units = number(20, 5, 2000, 1);
    const files = el("input", {type: "file", multiple: true});
    files.className = "colab-file-input";
    const selectedFilesLabel = el("span", {class: "muted", text: "尚未選取檔案"});
    const chooseFiles = button("選擇參考檔案", () => files.click());
    files.addEventListener("change", () => { selectedFilesLabel.textContent = [...files.files].map(file => file.name).join("、") || "尚未選取檔案"; });
    const fileNote = el("span", {class: "muted"});
    const modeField = field("影片方式", mode), resolutionField = field("影片尺寸", resolution), sizeField = field("圖片尺寸", size), secondsField = field("生成秒數", seconds), aspectField = field("畫面比例", aspect);
    const details = el("details", {class: "colab-limits"}, [el("summary", {text: "GPU 與執行上限"}), el("div", {class: "colab-grid"}, [field("GPU", gpu), field("最長執行分鐘", minutes), field("點數停止門檻", units)]), el("p", {class: "muted", text: "點數以帳號餘額變化監測，可能含其他執行階段的用量；這是停止門檻，並非 Google 的硬性扣款上限。環境準備也會使用點數。"})]);
    const generate = el("button", {type: "submit", class: "primary small", text: "在 Colab 生成"});
    const fileField = field("參考檔案（合計最多 32 MB）", files);
    fileField.append(el("div", {class: "colab-row"}, [chooseFiles, selectedFilesLabel]));
    form.append(el("div", {class: "colab-grid"}, [field("生成內容", kind), modeField, aspectField, resolutionField, sizeField, secondsField]), field("內容描述", prompt), fileField, fileNote, details, generate);
    root.append(form);
    const jobs = el("section", {class: "colab-jobs", "aria-label": "Colab 任務"});
    root.append(el("h3", {text: "最近任務"}), jobs);

    function renderAccounts() {
      const wanted = selectedId || account.value;
      account.replaceChildren(...snapshot.accounts.map(item => el("option", {value: item.id, text: item.label})));
      if (snapshot.accounts.some(item => item.id === wanted)) account.value = wanted;
      if (!snapshot.accounts.length) accountSettings.open = true;
      const selected = snapshot.accounts.find(item => item.id === account.value);
      balance.textContent = selected?.authenticated ? `可用 ${Number(selected.balance_compute_units || 0).toFixed(1)} 點` : "尚未連接";
      generate.disabled = !snapshot.installed || !selected?.authenticated;
      create.disabled = !snapshot.installed;
      refreshButton.disabled = reconnect.disabled = !snapshot.installed || !selected;
    }
    function renderJobs() {
      jobs.replaceChildren();
      if (!snapshot.jobs.length) { jobs.append(el("p", {class: "muted", text: "尚無 Colab 任務"})); return; }
      for (const job of snapshot.jobs) {
        const actions = el("div", {class: "colab-row"});
        for (const file of job.artifacts || []) actions.append(el("a", {href: file.url, target: "_blank", rel: "noopener", text: `查看成果 ${file.name}`}));
        if (!["completed", "failed", "cancelled"].includes(job.phase)) actions.append(button(job.cancel_requested ? "再次確認釋放" : "停止並釋放 GPU", () => run(async () => { await post(`jobs/${job.id}/cancel`); await refresh(); })));
        jobs.append(el("article", {class: "colab-job"}, [
          el("div", {class: "colab-row"}, [el("strong", {text: job.status_label}), el("span", {class: "muted", text: `${job.gpu} · ${new Date(job.created_at * 1000).toLocaleString("zh-TW")}`})]),
          el("p", {class: "muted", text: job.message || (job.remote_phase ? `執行階段：${({validating:"檢查設定", installing:"安裝環境與模型", starting:"啟動模型服務", submitting:"提交生成", running:"生成中", completed:"生成完成"})[job.remote_phase] || "處理中"}` : "")}), actions,
        ]));
      }
    }
    async function refresh() {
      snapshot = await api("/api/colab"); renderAccounts(); renderJobs();
    }
    function controls() {
      const video = kind.value === "video", image = kind.value === "image";
      modeField.hidden = resolutionField.hidden = !video;
      secondsField.hidden = image; sizeField.hidden = !image; aspectField.hidden = kind.value === "tts";
      files.disabled = video && mode.value === "text";
      chooseFiles.disabled = files.disabled;
      files.accept = image || (video && mode.value === "frames") ? "image/png,image/jpeg,image/webp" : kind.value === "tts" ? "audio/*" : "image/*,video/*,audio/*";
      fileNote.textContent = kind.value === "tts" ? "最多 3 段聲音參考。" : video && mode.value === "ref" ? "在描述中使用 @Image1、@Video1 或 @Audio1 指定各類參考素材；每個素材都必須提及。" : video && mode.value === "frames" ? "依序選取首幀與尾幀；也可以只提供首幀。" : video && mode.value === "extend" ? "選取一段影片；秒數是本次生成請求，新增片段會因銜接裁切而較短。" : image ? "最多 8 張圖片；有參考圖時會以第一張進行圖片編輯。" : "不需要參考檔案。";
      lastRequest = null; requestId = crypto.randomUUID();
    }
    account.addEventListener("change", () => {selectedId = account.value; renderAccounts(); lastRequest = null; requestId = crypto.randomUUID();});
    kind.addEventListener("change", controls); mode.addEventListener("change", controls);
    form.addEventListener("input", () => {lastRequest = null; requestId = crypto.randomUUID();});
    form.addEventListener("submit", event => {
      event.preventDefault();
      run(async () => {
        generate.disabled = true;
        try {
          if (!lastRequest) {
            const selectedFiles = files.disabled ? [] : [...files.files];
            if (selectedFiles.length > 8 || selectedFiles.reduce((sum, file) => sum + file.size, 0) > 32 * 1024 * 1024) throw new Error("參考檔案最多 8 個，合計上限為 32 MB");
            const job = {type: kind.value, prompt: prompt.value, seed: crypto.getRandomValues(new Uint32Array(1))[0]};
            if (kind.value === "image") Object.assign(job, {size: size.value, aspect: aspect.value});
            else Object.assign(job, {seconds: Number(seconds.value)});
            if (kind.value === "video") Object.assign(job, {mode: mode.value, resolution: resolution.value, aspect: aspect.value, fast: true, intensity: "off"});
            lastRequest = {request_id: requestId, account_id: account.value, gpu: gpu.value, max_minutes: Number(minutes.value), max_units: Number(units.value), job, files: await Promise.all(selectedFiles.map(fileData))};
          }
          await post("jobs", lastRequest);
          lastRequest = null; requestId = crypto.randomUUID(); files.value = "";
          selectedFilesLabel.textContent = "尚未選取檔案";
          await refresh();
        } finally { renderAccounts(); }
      });
    });
    renderAccounts(); renderJobs(); controls();
    async function poll() {
      if (!root.isConnected) return;
      try { await refresh(); } catch (_) { /* Keep existing state and editable fields during reconnect. */ }
      if (root.isConnected) setTimeout(poll, 10000);
    }
    setTimeout(poll, 10000);
  }

  window.WebComfyColab = {
    mount(host, options) {
      const action = button("開啟 Colab", () => open(action, options));
      host.append(el("section", {class: "panel colab-entry"}, [el("div", {}, [el("h2", {text: "Google Colab · H3"}), el("p", {class: "muted", text: "用自己的 Google 帳號執行生圖、影片與語音，分別管理 Ultra 與 Pro 的點數。"})]), action]));
    },
  };
})();
