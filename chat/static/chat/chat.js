(function () {
  const messagesEl = document.getElementById('messages');
  // 실제 overflow-y: auto 가 걸려 있는 스크롤 컨테이너는 messagesEl 부모(.chat-messages).
  // messagesEl 자체는 폭 제한용 .chat-frame 이라 scrollTop 조작해도 움직이지 않는다.
  const scrollContainer = document.querySelector('.chat-messages');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('userInput');
  const sendBtn = document.getElementById('sendBtn');
  const resetBtn = document.getElementById('resetBtn');

  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

  const MESSAGE_URL = '/message/';
  const RESET_URL = '/reset/';
  const FEEDBACK_URL = '/feedback/';

  // ─── 메시지 렌더링 ───

  function createAvatar(kind) {
    const el = document.createElement('div');
    el.className = 'msg-avatar msg-avatar-' + kind;
    if (kind === 'bot') {
      const img = document.createElement('img');
      img.src = '/media/icon/icon.png';
      img.alt = 'TA9';
      img.onerror = () => { img.style.display = 'none'; };
      el.appendChild(img);
    } else {
      el.textContent = 'U';
    }
    return el;
  }

  function addUserMessage(text) {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-user';

    const main = document.createElement('div');
    main.className = 'msg-main';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = text;
    main.appendChild(bubble);
    main.appendChild(createAvatar('user'));

    wrap.appendChild(main);
    messagesEl.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  function addBotMessage(text, { isError = false, sources = [], chatLogId = null } = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-bot';

    // 상단: 아바타 + 버블 (같은 행, 아바타는 버블 하단에 정렬)
    const main = document.createElement('div');
    main.className = 'msg-main';
    main.appendChild(createAvatar('bot'));

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (isError) bubble.style.color = '#c00';
    renderMarkdown(bubble, text);
    main.appendChild(bubble);

    wrap.appendChild(main);

    // 하단: 출처·피드백 (버블 아래에 별도로, 아바타 영향 받지 않음)
    if ((sources && sources.length > 0) || chatLogId) {
      const extras = document.createElement('div');
      extras.className = 'msg-extras';

      if (sources && sources.length > 0) {
        const sourcesWrap = document.createElement('div');
        sourcesWrap.className = 'msg-sources';
        sources.forEach((src) => sourcesWrap.appendChild(createSourceBadge(src)));
        extras.appendChild(sourcesWrap);
      }

      if (chatLogId) {
        extras.appendChild(createFeedbackButtons(chatLogId));
      }

      wrap.appendChild(extras);
    }

    messagesEl.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  // 봇 버블 전용 마크다운 → HTML 렌더 (DOMPurify로 sanitize)
  function renderMarkdown(el, text) {
    const raw = text || '';
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
      // 라이브러리 로딩 실패 시 안전하게 평문으로
      el.textContent = raw;
      return;
    }
    try {
      const html = marked.parse(raw, { breaks: true, gfm: true });
      el.innerHTML = DOMPurify.sanitize(html);
      // 외부 링크는 새 탭으로
      el.querySelectorAll('a[href]').forEach((a) => {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      });
    } catch (_) {
      el.textContent = raw;
    }
  }

  function createSourceBadge(src) {
    const el = document.createElement('a');
    el.className = 'source-badge';
    el.textContent = `📄 ${src.name}`;
    el.href = src.url || '#';
    el.title = src.name;

    const isPdf = /\.pdf$/i.test(src.name);
    if (isPdf) {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        openPdfModal(src);
      });
    } else {
      el.target = '_blank';
      el.rel = 'noopener';
    }
    return el;
  }

  // ─── 피드백 ───

  function createFeedbackButtons(chatLogId) {
    const wrap = document.createElement('div');
    wrap.className = 'msg-feedback';

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'feedback-btn';
    upBtn.title = '좋은 답변';
    upBtn.textContent = '👍';

    const downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'feedback-btn';
    downBtn.title = '나쁜 답변';
    downBtn.textContent = '👎';

    // 이미 이 QAPair에 피드백 준 적 있으면 해당 상태로 복원
    const storedRating = localStorage.getItem(`feedback_chatlog_${chatLogId}`);
    if (storedRating === 'up') {
      upBtn.classList.add('active-up');
      downBtn.disabled = true;
      upBtn.disabled = true;
    } else if (storedRating === 'down') {
      downBtn.classList.add('active-down');
      downBtn.disabled = true;
      upBtn.disabled = true;
    }

    upBtn.addEventListener('click', () => sendFeedback(chatLogId, 'up', upBtn, downBtn));
    downBtn.addEventListener('click', () => sendFeedback(chatLogId, 'down', upBtn, downBtn));

    wrap.appendChild(upBtn);
    wrap.appendChild(downBtn);
    return wrap;
  }

  async function sendFeedback(chatLogId, rating, upBtn, downBtn) {
    // 낙관적 UI — 먼저 버튼 상태 업데이트
    upBtn.disabled = true;
    downBtn.disabled = true;
    if (rating === 'up') upBtn.classList.add('active-up');
    else downBtn.classList.add('active-down');
    localStorage.setItem(`feedback_chatlog_${chatLogId}`, rating);

    try {
      const res = await fetch(FEEDBACK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ chat_log_id: chatLogId, rating }),
      });
      if (!res.ok) throw new Error('피드백 전송 실패');
    } catch (_) {
      // 실패해도 UI는 그대로 두지만, 사용자가 새로고침 후 다시 시도 가능하도록 localStorage 해제
      localStorage.removeItem(`feedback_chatlog_${chatLogId}`);
    }
  }

  // ─── PDF 모달 ───

  function openPdfModal(src) {
    closePdfModal();
    const overlay = document.createElement('div');
    overlay.className = 'pdf-modal-overlay';
    overlay.id = 'pdfModalOverlay';
    overlay.innerHTML = `
      <div class="pdf-modal" role="dialog" aria-modal="true">
        <header class="pdf-modal-header">
          <div class="pdf-modal-title"></div>
          <button type="button" class="pdf-modal-close" aria-label="닫기">✕</button>
        </header>
        <div class="pdf-modal-body">
          <iframe src="${src.url}" title="PDF 뷰어"></iframe>
        </div>
      </div>
    `;
    overlay.querySelector('.pdf-modal-title').textContent = src.name;
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closePdfModal();
    });
    overlay.querySelector('.pdf-modal-close').addEventListener('click', closePdfModal);
    document.addEventListener('keydown', escHandler);
    document.body.appendChild(overlay);
  }

  function closePdfModal() {
    const overlay = document.getElementById('pdfModalOverlay');
    if (overlay) overlay.remove();
    document.removeEventListener('keydown', escHandler);
  }

  function escHandler(e) {
    if (e.key === 'Escape') closePdfModal();
  }

  // ─── 공용 헬퍼 ───

  function addThinkingIndicator() {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-bot msg-thinking';

    const main = document.createElement('div');
    main.className = 'msg-main';
    main.appendChild(createAvatar('bot'));
    main.innerHTML += `
      <div class="msg-bubble">
        <span class="typing-dots">
          <span></span><span></span><span></span>
        </span>
      </div>
    `;
    wrap.appendChild(main);

    messagesEl.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  function scrollToBottom() {
    // 메시지 append 직후 호출되면 아직 레이아웃이 갱신 전일 수 있어
    // 다음 프레임에 실제 scrollHeight 기준으로 내린다.
    requestAnimationFrame(() => {
      scrollContainer.scrollTop = scrollContainer.scrollHeight;
    });
  }

  function setInputEnabled(enabled) {
    input.disabled = !enabled;
    sendBtn.disabled = !enabled;
    if (enabled) input.focus();
  }

  // ─── 서버 호출 ───

  async function fetchBotReply(userText) {
    const res = await fetch(MESSAGE_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ message: userText }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || '요청이 실패했습니다.');
    }
    return {
      reply: data.reply || '',
      sources: data.sources || [],
      chatLogId: data.chat_log_id || null,
    };
  }

  async function handleSubmit(text) {
    if (!text) return;

    addUserMessage(text);
    setInputEnabled(false);

    const thinkingEl = addThinkingIndicator();
    try {
      const { reply, sources, chatLogId } = await fetchBotReply(text);
      thinkingEl.remove();
      addBotMessage(reply, { sources, chatLogId });
    } catch (err) {
      thinkingEl.remove();
      addBotMessage(err.message || '오류가 발생했습니다.', { isError: true });
    } finally {
      setInputEnabled(true);
    }
  }

  // ─── 이벤트 바인딩 ───

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    input.value = '';
    handleSubmit(text);
  });

  resetBtn.addEventListener('click', async () => {
    try {
      await fetch(RESET_URL, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
      });
    } catch (_) {}
    messagesEl.innerHTML = '';
    addBotMessage('안녕하세요! TA9 챗봇입니다.\n무엇을 도와드릴까요?');
    setInputEnabled(true);
  });

  input.focus();
})();
