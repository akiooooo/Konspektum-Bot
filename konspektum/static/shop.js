'use strict';
const tg=window.Telegram?.WebApp;
const root=document.querySelector('#app');
const modalRoot=document.querySelector('#modal-root');
const state={data:null,tgAuth:tg?.initData||'',cart:{},page:'catalog',admin:null,adminTab:'orders',orderFilter:'all',search:'',modal:null,busy:false,lastFocus:null,
  deadline:'Не спешу',note:'',thread:null,replyKey:null,loginTimer:null,loginGeneration:0,refreshTimer:null};
const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rub=n=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:n%100?2:0}).format(n/100);
const service=id=>state.data.services.find(s=>s.id===Number(id));
const items=()=>Object.entries(state.cart).map(([id,quantity])=>({...service(id),quantity})).filter(s=>s.id&&s.quantity>0);
const total=()=>items().reduce((n,s)=>n+s.quantity*s.price,0);
const key=()=>crypto.randomUUID();
const date=value=>new Date(value.replace(' ','T')+'Z').toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const link=(url,label,cls='')=>`<a class="${cls}" href="${h(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;

async function api(path,options={}){
  const upload=options.body instanceof FormData;
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{...(!upload?{'Content-Type':'application/json'}:{}),
    ...(state.tgAuth?{Authorization:`tma ${state.tgAuth}`} :{}),
    ...(state.data?.csrf?{'X-CSRF-Token':state.data.csrf}:{}),...options.headers}});
  const data=await response.json();
  if(!response.ok){if(response.status===401&&state.tgAuth){state.tgAuth='';if(path==='/api/bootstrap')return api(path,options);}const e=new Error(data.error||'Не удалось выполнить запрос.');e.status=response.status;if(response.status===401&&state.data?.user){state.data={...state.data,user:null,is_admin:false,csrf:''};state.admin=null;state.busy=false;closeModal();state.page='orders';render();toast('Выполнен выход. Войдите заново или переоткройте магазин из бота.');}throw e;}
  return data;
}
let toastTimer;
function toast(text){const el=document.querySelector('#toast');el.textContent=text;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),5500);}
function persist(){try{sessionStorage.setItem('konspektum.cart',JSON.stringify(state.cart));}catch{}}
function normalize(){state.cart=Object.fromEntries(Object.entries(state.cart).filter(([id,q])=>service(id)&&Number.isInteger(q)&&q>0).map(([id,q])=>[id,Math.min(q,service(id).max_quantity)]));persist();}
function stepper(s,prefix=''){
  return `<div class="stepper"><button type="button" data-action="minus" data-id="${s.id}" aria-label="Уменьшить ${h(s.title)}">−</button>
    <label class="sr-only" for="qty-${prefix}${s.id}">Количество: ${h(s.title)}</label><input id="qty-${prefix}${s.id}" data-quantity="${s.id}" type="number" inputmode="numeric" min="0" max="${s.max_quantity}" step="1" value="${state.cart[s.id]||0}">
    <button type="button" data-action="plus" data-id="${s.id}" aria-label="Добавить ${h(s.title)}">＋</button></div>`;
}
function header(){
  const user=state.data.user;
  return `<header class="topbar"><a class="brand" href="#catalog" data-page="catalog"><span class="brand-icon">к.</span>конспектум</a>
    <nav class="topnav" aria-label="Главное меню">${[['catalog','Каталог'],['cart','Корзина'],['orders','Мои заказы'],['about','Помощь'],...(state.data.is_admin?[['admin','Управление']]:[])].map(([p,l])=>`<button class="nav-item ${state.page===p?'active':''}" data-page="${p}" ${state.page===p?'aria-current="page"':''}>${l}${p==='cart'&&items().length?` <span class="nav-count">${items().length}</span>`:''}</button>`).join('')}</nav>
    <div class="profile">${user?`<span class="avatar">${h((user.first_name||'К').slice(0,1))}</span><span class="profile-name">${h(user.first_name||'Покупатель')}</span>${!tg?.initData?'<button class="text-link" data-action="logout">Выйти</button>':''}`:'<button class="login-pill" data-action="login">Войти через Telegram ↗</button>'}</div></header>`;
}
function footer(){return `<footer class="footer"><span><span class="footer-brand">конспектум</span> &nbsp; · &nbsp; время для важного</span><div class="footer-links"><button data-page="about">Помощь</button>${link(state.data.contact_url,'Написать нам ↗')}<button data-action="login">Вход для владельца</button></div><span>Ваши материалы. Наша аккуратная работа.</span></footer>`;}
function card(s,i){return `<article class="service-card"><div class="card-top"><span class="service-icon">${h(s.icon)}</span><span class="card-hint">${String(i+1).padStart(2,'0')} / КОНСПЕКТУМ</span></div><h3>${h(s.title)}</h3><p>${h(s.description)}</p><div class="card-bottom"><span class="service-price">${rub(s.price)} <span class="unit">/ ${h(s.unit)}</span></span>${state.cart[s.id]?stepper(s):`<button class="add-btn" data-action="plus" data-id="${s.id}"><span class="plus">＋</span> В корзину</button>`}</div></article>`;}
function cartPanel(cta=true){
  const rows=items();
  return `<section class="cart-panel" aria-label="Корзина"><div class="cart-title"><h2>Ваш заказ</h2><span class="mini-count">${rows.length}</span></div>${rows.length?`<div class="cart-items">${rows.map(s=>`<div class="cart-item" data-cart-row="${s.id}"><div class="cart-item-top"><span>${h(s.title)}</span><strong>${rub(s.price*s.quantity)}</strong></div><div class="cart-item-detail">${stepper(s,'cart-')}<span class="muted">${rub(s.price)} / ${h(s.unit)}</span></div></div>`).join('')}</div>`:'<div class="cart-empty"><div class="empty-art">✎</div><h3>Пока чистый лист</h3><p>Выберите услуги и количество —<br>мы рассчитаем стоимость</p></div>'}<div class="cart-divider"><div class="total-row"><span>Предварительно</span><strong>${rub(total())}</strong></div><p class="small-note">Точная стоимость — после согласования материалов</p>${cta?`<button class="btn full" data-page="cart" ${!rows.length?'disabled':''}>Выбрать срок <span>→</span></button>`:''}<div class="lock-note">Согласуем оплату в чате заказа</div></div></section>`;
}
function process(){return `<div class="process"><p class="process-label">ОТ ВЫБОРА ДО КОНСПЕКТА</p>${[['Соберите заказ','Услуги, объём и желаемый срок'],['Перейдите в Telegram','Бот сохранит ваш выбор автоматически'],['Обсудим детали','Пришлите материалы и согласуйте оплату']].map(([t,d],i)=>`<div class="process-step"><span class="step-number">${i+1}</span><div><h4>${t}</h4><p>${d}</p></div></div>`).join('')}<div class="contact-small">Нужна помощь? ${link(state.data.contact_url,'Напишите нам ↗')}</div></div>`;}
function catalog(){return `<div class="layout"><main><section class="hero"><div class="hero-copy"><div class="intro-label"><span class="line"></span>КОНСПЕКТЫ ПО ВАШИМ МАТЕРИАЛАМ</div><h1>Меньше писать.<br>Больше <em>успевать.</em></h1><p>Перенесём ваши материалы в аккуратный конспект на машинке. А свободное время останется вам.</p><button class="hero-cta" data-action="to-services">Выбрать услуги <span>↓</span></button><div class="hero-tags"><span class="tag">✦ Понятный расчёт</span><span class="tag">♡ Личное общение</span></div></div><img src="/assets/typewriter.svg" class="hero-art" alt="Пишущая машинка с листом конспекта"></section><div class="trust-strip"><span>01 &nbsp; Выбираете объём</span><i>→</i><span>02 &nbsp; Указываете срок</span><i>→</i><span>03 &nbsp; Продолжаете в Telegram</span></div><div class="section-head" id="services"><div class="section-title"><h2>Что напишем?</h2><span class="count">${String(state.data.services.length).padStart(2,'0')}</span></div><span class="section-note">Выберите одну или несколько услуг</span></div><div class="services">${state.data.services.map(card).join('')||'<div class="empty-list">Каталог обновляется. Напишите нам — поможем оформить заказ.</div>'}</div><div class="how-strip"><span class="spark">✧</span><p><strong>Не уверены, сколько страниц получится?</strong><br>Укажите примерный объём. Окончательное количество и стоимость согласуем по вашим материалам.</p></div></main><aside class="aside">${cartPanel()}${process()}</aside></div>`;}
function checkoutForm(){return `<form id="checkout-form" class="content-panel"><div class="intro-label">ШАГ 02 / СРОК И ПОЖЕЛАНИЯ</div><h2>Когда нужен конспект?</h2><div class="deadline-options">${state.data.deadlines.map((d,i)=>`<label class="deadline-option"><input type="radio" name="deadline" value="${h(d)}" ${state.deadline===d?'checked':''} required><span><strong>${['Без спешки','В течение недели','За 2–3 дня','Срочный заказ'][i]}</strong><small>${['Согласуем удобную дату','Есть время на детали','Уточним доступность','Подтвердим в чате заказа'][i]}</small></span><i>✓</i></label>`).join('')}</div><label class="field">Что нужно учесть? <span class="optional">необязательно</span><textarea name="note" maxlength="1000" placeholder="Предмет, формат тетради, пожелания к оформлению…">${h(state.note)}</textarea><small>Фото и материалы отправите в чат заказа в боте.</small></label><div class="checkout-next"><span class="step-number">3</span><p><strong>Продолжим в Telegram</strong><br>Откроется бот. Нажмите «Начать» — он автоматически создаст заказ с выбранными услугами, сроком и пожеланиями.</p></div><p class="privacy">Для выполнения заказа магазин сохранит ваш Telegram ID, имя и сведения о заказе. Итоговую стоимость, дату готовности и оплату согласуем в чате заказа. Не указывайте здесь платёжные данные.</p><div class="form-error" role="alert" hidden></div><button class="btn full" type="submit" ${!items().length?'disabled':''}>Перейти в Telegram <span>↗</span></button></form>`;}
function cartPage(){return `<main class="page-width wide"><div class="page-heading"><div class="intro-label">УСЛУГИ → СРОК → TELEGRAM</div><h1>Осталось несколько штрихов.</h1><p>Проверьте объём, выберите срок — и продолжим лично.</p></div>${items().length?`<div class="checkout-grid"><div>${checkoutForm()}</div><div>${cartPanel(false)}<button class="btn outline full" data-page="catalog">＋ Добавить услуги</button></div></div>`:'<section class="content-panel empty-list"><div class="empty-art">✎</div><h2>Пока чистый лист</h2><p>Добавьте услуги, чтобы выбрать срок и оформить заказ.</p><button class="btn" data-page="catalog">Открыть каталог →</button></section>'}</main>`;}
function about(){return `<main class="page-width"><div class="page-heading"><div class="intro-label">КОНСПЕКТУМ / ПОМОЩЬ</div><h1>Всё начинается с разговора.</h1><p>${h(state.data.settings.welcome)}</p></div><section class="content-panel"><h2>Как сделать заказ?</h2><p>Выберите услуги и количество страниц или элементов. В корзине укажите желаемый срок и пожелания. Кнопка «Перейти в Telegram» откроет бота: после нажатия «Начать» заказ создастся автоматически. Затем отправьте материалы в чат заказа в этом боте.</p></section><div class="faq-grid">${[['Как считать страницы?','Укажите примерное количество. Формат тетради, плотность записи и окончательный объём согласуем по вашим материалам.'],['Как оплатить?','Оплата — напрямую магазину после обсуждения материалов, стоимости и сроков. На сайте и в боте нет встроенной оплаты.'],['Когда будет готово?','Выберите пожелание по сроку при оформлении. Возможность срочного выполнения и точную дату подтвердим в чате заказа в боте.'],['Как узнать статус?','Войдите через Telegram и откройте «Мои заказы». При смене статуса бот также пришлёт уведомление в Telegram.']].map(([q,a])=>`<section class="content-panel"><h2>${q}</h2><p>${a}</p></section>`).join('')}</div><section class="content-panel"><h2>О магазине</h2><p>${h(state.data.settings.about)}</p>${link(state.data.contact_url,'Написать нам ↗','btn')}</section><section class="content-panel"><h2>Ваши данные</h2><p>Сохраняем имя, Telegram ID, username, сведения о заказах и переписку с магазином для выполнения заказа. Эти данные доступны вам и администраторам. Выбор с сайта до перехода в Telegram хранится не более часа. Вход подтверждается в боте, сессия сайта действует один час. Для удаления данных обратитесь к магазину. Не передавайте пароли и реквизиты карт в комментариях и сообщениях.</p></section></main>`;}
function ordersPage(){return `<main class="page-width"><div class="page-heading"><div class="intro-label">КОНСПЕКТУМ / МОИ ЗАКАЗЫ</div><h1>Всё под рукой.</h1><p>Состав, сроки, статус и разговор с магазином.</p></div><div id="orders-list" aria-live="polite">${state.data.user?'<div class="empty-list">Загружаем заказы…</div>':'<section class="content-panel login-card"><span class="service-icon">↗</span><h2>Ваши заказы — только для вас</h2><p>Войдите через Telegram. Пароль не нужен: достаточно подтвердить вход в боте.</p><button class="btn" data-action="login">Войти через Telegram ↗</button></section>'}</div></main>`;}
function orderCard(o,admin=false){return `<article class="order-card"><div class="order-head"><span class="order-code">${h(o.code)}</span><span class="status ${h(o.status)}">${h(state.data.statuses[o.status])}</span></div>${admin?`<p class="order-line">${h(o.name)} · ${o.username?'@'+h(o.username):'ID '+o.user_id}</p>`:''}${o.items.map(s=>`<p class="order-line">${h(s.title)} · ${s.quantity} × ${rub(s.price)} = ${rub(s.subtotal)}</p>`).join('')}<p class="order-line">${h(o.deadline)} · ${date(o.created_at)}</p>${o.note?`<p class="order-note">${h(o.note)}</p>`:''}<div class="order-footer"><strong>${rub(o.total)}</strong><div class="row-actions"><button class="btn secondary" data-action="thread" data-id="${o.id}">${admin?'Ответить в Telegram':'Переписка'} ↗</button>${admin?`<label class="sr-only" for="status-${o.id}">Статус заказа</label><select class="status-select" id="status-${o.id}" data-status="${o.id}">${Object.entries(state.data.statuses).map(([v,l])=>`<option value="${v}" ${o.status===v?'selected':''}>${l}</option>`).join('')}</select><button class="btn outline" data-action="delete-order" data-id="${o.id}">Удалить…</button>`:link(o.contact_url,'Чат заказа в боте ↗','btn')}</div></div></article>`;}
async function loadOrders(){if(!state.data.user)return;try{const data=await api('/api/orders');const el=document.querySelector('#orders-list');if(el)el.innerHTML=data.orders.map(o=>orderCard(o)).join('')||'<div class="content-panel empty-list">Здесь появится ваш первый заказ.<br><button class="btn" data-page="catalog">Выбрать услуги →</button></div>';}catch(e){toast(e.message);}}
function adminPage(){if(!state.data.is_admin)return '<main class="empty-list">Для управления магазином войдите с аккаунта администратора.</main>';return `<main class="page-width wide"><div class="page-heading"><div class="intro-label">КОНСПЕКТУМ / УПРАВЛЕНИЕ</div><h1>Рабочий стол магазина.</h1><p>Заказы, переписка и каталог. Все изменения синхронизируются с Telegram.</p></div><div class="admin-tabs">${[['orders','Заказы и сообщения'],['services','Услуги и цены'],['settings','Настройки']].map(([v,l])=>`<button data-admin-tab="${v}" class="${state.adminTab===v?'active':''}">${l}</button>`).join('')}</div><div id="admin-content">${adminContent()}</div></main>`;}
function adminOrderList(){const q=state.search.toLocaleLowerCase('ru-RU');return state.admin.orders.filter(o=>(state.orderFilter==='all'||o.status===state.orderFilter)&&[o.code,o.name,o.username,o.note,...o.items.map(i=>i.title)].join(' ').toLocaleLowerCase('ru-RU').includes(q)).map(o=>orderCard(o,true)).join('')||'<div class="empty-list">Заказов по этому фильтру пока нет.</div>';}
function adminContent(){const a=state.admin;if(!a)return '<div class="empty-list">Загружаем данные…</div>';if(state.adminTab==='orders')return `<div class="stats-grid"><div><span>В работе и обсуждении</span><strong>${a.orders.filter(o=>['new','discussing','in_progress'].includes(o.status)).length}</strong></div><div><span>Готовые заказы</span><strong>${a.orders.filter(o=>o.status==='done').length}</strong></div><div><span>Всего в списке</span><strong>${a.orders.length}</strong></div></div><div class="order-toolbar"><label class="sr-only" for="order-search">Поиск заказов</label><input id="order-search" placeholder="Номер, клиент или предмет…" value="${h(state.search)}"><label class="sr-only" for="order-filter">Статус</label><select id="order-filter"><option value="all">Все статусы</option>${Object.entries(state.data.statuses).map(([v,l])=>`<option value="${v}" ${state.orderFilter===v?'selected':''}>${l}</option>`).join('')}</select><button class="btn outline" data-action="refresh-admin">Обновить ↻</button></div><p class="small-note">Последние 100 заказов · Покупатель получает уведомление при смене статуса.</p><div id="admin-order-list">${adminOrderList()}</div>`;
  if(state.adminTab==='services')return `<div class="section-head"><p class="small-note">Изменения сразу доступны в боте и на сайте.</p><button class="btn" data-action="new-service">＋ Добавить услугу</button></div><div class="admin-services">${a.services.map(s=>`<article class="service-card ${!s.active?'hidden-service':''}"><div class="card-top"><span class="service-icon">${h(s.icon)}</span><span class="status">${s.active?'В каталоге':'Скрыта'}</span></div><h3>${h(s.title)}</h3><p>${h(s.description)}</p><div class="card-bottom"><span class="service-price">${rub(s.price)} <span class="unit">/ ${h(s.unit)}</span></span><button class="btn secondary" data-action="edit-service" data-id="${s.id}">Изменить</button></div></article>`).join('')}</div>`;
  return `<form id="settings-form" class="content-panel"><h2>Контакты и тексты</h2><label class="field">Контакт владельца<input name="shop_username" value="${h(a.settings.shop_username)}" required maxlength="33"><small>Справочный контакт владельца. Общение и материалы передаются через чат заказа в боте.</small></label><label class="field">Чат уведомлений<input name="notify_chat_id" value="${h(a.settings.notify_chat_id)}" maxlength="50" placeholder="Числовой Telegram ID"><small>Оставьте пустым, чтобы уведомлять администраторов. Для группы укажите её числовой ID и добавьте бота с правом отправки сообщений.</small></label><label class="field">Приветствие<input name="welcome" value="${h(a.settings.welcome)}" required maxlength="120"></label><label class="field">О магазине<textarea name="about" required maxlength="1000">${h(a.settings.about)}</textarea></label><div class="form-error" role="alert" hidden></div><button class="btn" type="submit">Сохранить везде</button></form>`;
}
async function loadAdmin(){if(!state.data.is_admin)return;state.admin=await api('/api/admin/data');const el=document.querySelector('#admin-content');if(el)el.innerHTML=adminContent();}
function render(){root.innerHTML=`<div class="shell">${header()}${({catalog,cart:cartPage,orders:ordersPage,about,admin:adminPage}[state.page]||catalog)()}${footer()}</div>${state.page==='catalog'?`<div class="mobile-cart"><div><p>${items().length?'Ваш заказ · '+items().length+' поз.':'Выберите услуги и объём'}</p><strong>${rub(total())}</strong></div><button class="btn" data-page="cart">К заказу <span>→</span></button></div>`:''}`;}
async function navigate(page){if(!['catalog','cart','orders','about','admin'].includes(page)||state.busy)return;closeModal();state.page=page;render();history.replaceState(null,'','#'+page);window.scrollTo({top:0,behavior:'instant'});try{if(page==='orders')await loadOrders();if(page==='admin')await loadAdmin();}catch(e){toast(e.message);}}
function quantity(id,value,redraw=true){const s=service(id),q=Number(value);if(!s)return;if(!Number.isInteger(q)||q<0||q>s.max_quantity){toast(`Количество: от 0 до ${s.max_quantity}.`);document.querySelectorAll(`[data-quantity="${id}"]`).forEach(el=>el.value=state.cart[id]||0);return;}if(q)state.cart[id]=q;else delete state.cart[id];persist();if(redraw){render();return;}document.querySelectorAll(`[data-quantity="${id}"]`).forEach(el=>el.value=q);document.querySelectorAll('.total-row strong,.mobile-cart strong').forEach(el=>el.textContent=rub(total()));document.querySelectorAll('.mini-count').forEach(el=>el.textContent=items().length);document.querySelectorAll('[data-cart-row]').forEach(el=>{const sid=Number(el.dataset.cartRow);el.querySelector('.cart-item-top strong').textContent=rub((state.cart[sid]||0)*(service(sid)?.price||0));});document.querySelectorAll('#checkout-form [type="submit"]').forEach(el=>el.disabled=!items().length);}
function modalHead(title){return `<div class="modal-header"><h2 id="modal-title">${title}</h2><button class="close-btn" data-action="close" aria-label="Закрыть">×</button></div>`;}
function openModal(content,kind){state.lastFocus=document.activeElement;state.modal=kind;modalRoot.innerHTML=`<div class="modal-backdrop"><section class="modal ${kind==='thread'?'thread-modal':''}" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1">${content}</section></div>`;root.inert=true;document.body.classList.add('modal-open');tg?.BackButton?.show();modalRoot.querySelector('.modal').focus();}
function closeModal(){if(state.busy)return;state.loginGeneration++;clearTimeout(state.loginTimer);state.modal=null;state.thread=null;modalRoot.innerHTML='';root.inert=false;document.body.classList.remove('modal-open');tg?.BackButton?.hide();if(state.lastFocus?.isConnected)state.lastFocus.focus();}
async function login(){
  if(state.data.user){await navigate(state.data.is_admin?'admin':'orders');return;}
  clearTimeout(state.loginTimer);
  const generation=++state.loginGeneration;
  const active=()=>state.modal==='login'&&generation===state.loginGeneration;
  openModal(`${modalHead('Войти через Telegram')}<p class="success-note">Готовим безопасный вход…</p>`,'login');
  try{
    const data=await api('/api/auth/start',{method:'POST',body:'{}'});
    if(!active())return;
    modalRoot.querySelector('.modal').innerHTML=`${modalHead('Это точно вы?')}<p class="success-note">Откройте бота и подтвердите вход.<br>Код в Telegram должен совпасть с этим:</p><div class="login-code">${h(data.code)}</div>${link(data.login_url,'Открыть Telegram ↗','btn full')}<p class="privacy">Подтверждайте только начатый вами вход. Вернитесь на эту страницу — она обновится автоматически. Код действует 5 минут.</p><p id="login-state" class="small-note">Ожидаем подтверждение в боте…</p>`;
    const until=Date.now()+data.expires_in*1000;
    const poll=async()=>{
      if(!active())return;
      try{
        const result=await api(data.poll_url||'/api/auth/poll');
        if(!active())return;
        if(result.authenticated){
          const fresh=await api('/api/bootstrap');
          if(!active())return;
          state.data=fresh;closeModal();render();await navigate(state.data.is_admin?'admin':'orders');toast('Вход подтверждён.');return;
        }
        if(Date.now()<until)state.loginTimer=setTimeout(poll,2500);
        else document.querySelector('#login-state').textContent='Время истекло. Закройте окно и начните вход заново.';
      }catch(e){
        if(!active())return;
        const el=document.querySelector('#login-state');
        if(el)el.textContent=e.status===400?e.message:'Нет связи. Повторяем проверку входа…';
        if(e.status!==400&&Date.now()<until)state.loginTimer=setTimeout(poll,3500);
      }
    };
    state.loginTimer=setTimeout(poll,300);
  }catch(e){if(active()){const el=modalRoot.querySelector('.success-note');if(el)el.textContent=e.message;}}
}
function serviceForm(id){const s=id?state.admin.services.find(s=>s.id===Number(id)):{title:'',description:'',unit:'страница',price:4500,icon:'✎',max_quantity:500,active:true};if(!s)return;openModal(`${modalHead(id?'Редактировать услугу':'Новая услуга')}<form id="service-form" data-id="${id||''}"><label class="field">Название<input name="title" value="${h(s.title)}" required maxlength="80"></label><div class="form-grid"><label class="field">Цена, ₽<input name="price" type="number" min="0.01" max="1000000" step="0.01" value="${s.price/100}" required></label><label class="field">За единицу<input name="unit" value="${h(s.unit)}" maxlength="30" required></label></div><label class="field">Описание<textarea name="description" maxlength="400">${h(s.description)}</textarea></label><div class="form-grid"><label class="field">Значок<input name="icon" value="${h(s.icon)}" maxlength="8" required></label><label class="field">Максимальное количество<input name="max_quantity" type="number" min="1" max="5000" step="1" value="${s.max_quantity}" required></label></div><label class="field checkbox"><input name="active" type="checkbox" ${s.active?'checked':''}> Показывать в каталоге</label><p class="privacy">Снимите галочку, чтобы убрать услугу из продажи. История заказов сохранится. Изменения действуют и в Telegram, и на сайте.</p><div class="form-error" role="alert" hidden></div><button class="btn full" type="submit">Сохранить везде</button></form>`,'service');}
const deliveryLabels={sent:'Доставлено в Telegram',received:'Получено',sending:'Отправляется',failed:'Не доставлено: бот недоступен покупателю',unknown:'Доставка не подтверждена — сообщение могло дойти'};
function threadMessages(data){return data.messages.map(m=>`<div class="chat-bubble ${m.direction==='out'?'out':'in'}"><span class="chat-author">${m.direction==='out'?'Конспектум':'Покупатель'}</span><p>${h(m.body)}</p>${m.attachment?`<button class="btn secondary" data-action="download" data-id="${m.id}" ${!m.attachment.file_id?'disabled':''}>↓ ${m.attachment.kind==='photo'?'Посмотреть фото':h(m.attachment.name||'Вложение')}</button>`:''}<small>${date(m.created_at)} · ${deliveryLabels[m.state]||h(m.state)}</small></div>`).join('')||'<p class="empty-list">Переписка по этому заказу пока не началась.</p>';}
async function openThread(id){state.replyKey=key();openModal(`${modalHead('Переписка')}<div class="empty-list">Открываем сообщения…</div>`,'thread');try{const data=await api(`/api/orders/${id}/messages`);if(state.modal!=='thread')return;state.thread=data;modalRoot.querySelector('.modal').innerHTML=`${modalHead(h(data.order.code))}<div class="thread-caption">${h(data.order.name)} · ${h(state.data.statuses[data.order.status])}</div><div class="chat-messages" id="chat-messages">${threadMessages(data)}</div>${state.data.is_admin?'<form id="reply-form"><label class="field">Ответ покупателю<textarea name="body" maxlength="2000" placeholder="Сообщение будет отправлено от имени бота в Telegram…"></textarea></label><label class="field">Фото (необязательно)<input type="file" name="photo" accept="image/jpeg,image/png,image/webp"><small>JPEG, PNG или WebP до 8 МБ. Подпись к фото — до 900 символов.</small></label><div class="form-error" role="alert" hidden></div><div class="form-actions"><button class="btn" type="submit">Отправить в Telegram ↗</button><button class="btn outline" type="button" data-action="refresh-thread">Обновить ↻</button></div><p class="privacy">Покупатель получит ответ в боте и сможет ответить кнопкой «Ответить по заказу». Сообщения сохраняются в этой переписке.</p></form>':`<p class="privacy">Чтобы ответить, откройте заказ в Telegram и отправьте сообщение или фото.</p>${link('https://t.me/'+state.data.bot_username+'?start=chat_'+id,'Открыть заказ в Telegram ↗','btn full')}`}`;scrollChat();}catch(e){modalRoot.querySelector('.empty-list').textContent=e.message;}}
async function downloadAttachment(id){
  if(!state.thread)return;
  const response=await fetch(`/api/orders/${state.thread.order.id}/messages/${id}/attachment`,{credentials:'same-origin',headers:state.tgAuth?{Authorization:`tma ${state.tgAuth}`}:{}});
  if(!response.ok){const data=await response.json();throw new Error(data.error||'Вложение недоступно.');}
  const blob=await response.blob();const url=URL.createObjectURL(blob);
  const row=state.thread.messages.find(m=>m.id===id),a=document.createElement('a');a.href=url;a.download=row?.attachment?.name||'Вложение';
  if(row?.attachment?.kind==='photo'){
    const button=modalRoot.querySelector(`[data-action="download"][data-id="${id}"]`);
    if(button){const image=document.createElement('img');image.src=url;image.alt='Фото к заказу';image.className='chat-photo';button.replaceWith(image);a.textContent='Скачать фото';a.className='text-link';image.after(a);}
  }else a.click();setTimeout(()=>URL.revokeObjectURL(url),300000);
}
function scrollChat(){const el=document.querySelector('#chat-messages');if(el)el.scrollTop=el.scrollHeight;}
async function refreshThread(){if(!state.thread)return;const data=await api(`/api/orders/${state.thread.order.id}/messages`);state.thread=data;const el=document.querySelector('#chat-messages');if(el){el.innerHTML=threadMessages(data);scrollChat();}}

document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-page],[data-action],[data-admin-tab]');if(!el)return;
  try{
    if(el.dataset.page){event.preventDefault();await navigate(el.dataset.page);return;}
    if(el.dataset.adminTab){state.adminTab=el.dataset.adminTab;render();return;}
    const action=el.dataset.action,id=Number(el.dataset.id);
    if(action==='plus'){quantity(id,(state.cart[id]||0)+1);tg?.HapticFeedback?.impactOccurred('light');}
    else if(action==='minus')quantity(id,(state.cart[id]||0)-1);
    else if(action==='to-services')document.querySelector('#services')?.scrollIntoView({behavior:'smooth'});
    else if(action==='login')await login();
    else if(action==='logout'){await api('/api/auth/logout',{method:'POST',body:'{}'});state.tgAuth='';state.data=await api('/api/bootstrap');state.admin=null;await navigate('catalog');}
    else if(action==='close')closeModal();
    else if(action==='new-service')serviceForm();
    else if(action==='edit-service')serviceForm(id);
    else if(action==='refresh-admin')await loadAdmin();
    else if(action==='delete-order'){
      const order=state.admin.orders.find(o=>o.id===id);if(!order)return;
      openModal(`${modalHead('Удалить заказ?')}<p>Заказ <strong>${h(order.code)}</strong> и его переписка будут удалены. Сообщения в Telegram останутся. Отменить удаление нельзя.</p><form id="delete-form" data-id="${id}" data-code="${h(order.code)}"><p class="privacy">Для подтверждения введите номер заказа: ${h(order.code)}</p><label class="field">Номер заказа<input name="confirmation" required autocomplete="off"></label><div class="form-error" role="alert" hidden></div><div class="form-actions"><button class="btn" type="submit">Удалить заказ</button><button class="btn outline" type="button" data-action="close">Отмена</button></div></form>`,'delete');
    }
    else if(action==='download')await downloadAttachment(id);
    else if(action==='thread')await openThread(id);
    else if(action==='refresh-thread')await refreshThread();
    else if(action==='retry')window.location.reload();
  }catch(e){toast(e.message);}
});
document.addEventListener('input',event=>{if(event.target.name==='note')state.note=event.target.value;if(event.target.id==='order-search'){state.search=event.target.value;document.querySelector('#admin-order-list').innerHTML=adminOrderList();}});
document.addEventListener('change',async event=>{const el=event.target;
  if(el.dataset.quantity){quantity(Number(el.dataset.quantity),el.value,false);return;}
  if(el.name==='deadline')state.deadline=el.value;
  if(el.id==='order-filter'){state.orderFilter=el.value;document.querySelector('#admin-order-list').innerHTML=adminOrderList();}
  if(el.dataset.status){el.disabled=true;try{const result=await api(`/api/admin/orders/${el.dataset.status}`,{method:'PATCH',body:JSON.stringify({status:el.value})});await loadAdmin();toast(result.notified===false?'Статус сохранён. Уведомление покупателю в очереди.':'Статус сохранён. Покупатель уведомлён.');}catch(e){toast(e.message);await loadAdmin().catch(()=>{});}finally{el.disabled=false;}}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['checkout-form','settings-form','service-form','reply-form','delete-form'].includes(form.id))return;event.preventDefault();if(state.busy)return;
  const values=Object.fromEntries(new FormData(form)),button=form.querySelector('[type="submit"]'),error=form.querySelector('.form-error');state.busy=true;button.disabled=true;error.hidden=true;
  try{
    if(form.id==='checkout-form'){
      const result=await api('/api/drafts',{method:'POST',body:JSON.stringify({cart:items().map(s=>({id:s.id,quantity:s.quantity,price:s.price})),deadline:values.deadline,note:values.note})});
      state.busy=false;openModal(`${modalHead('Продолжим в Telegram')}<div class="success-icon">↗</div><h3 class="success-title">Ваш выбор — с вами.</h3><p class="success-note">Нажмите «Начать» в боте.<br>Он автоматически создаст заказ с выбранным объёмом, сроком и пожеланиями.</p>${link(result.bot_url,'Открыть бота и создать заказ ↗','btn full')}<p class="privacy">Выбор сохранён на один час. Если Telegram не открылся автоматически, нажмите кнопку выше. Итоговую стоимость и оплату согласуем в чате заказа в боте.</p>`,'handoff');
      if(tg?.initData)tg.openTelegramLink(result.bot_url);else window.location.assign(result.bot_url);
    }else if(form.id==='delete-form'){
      await api(`/api/admin/orders/${form.dataset.id}`,{method:'DELETE',body:JSON.stringify({confirmation:values.confirmation})});
      state.busy=false;closeModal();await loadAdmin();toast('Заказ удалён.');
    }else if(form.id==='reply-form'){
      const photo=form.elements.photo.files[0];
      if(!values.body.trim()&&!photo)throw new Error('Напишите сообщение или выберите фото.');
      let result;
      if(photo){
        if(photo.size>8*1024*1024)throw new Error('Фото должно быть не больше 8 МБ.');
        if(values.body.length>900)throw new Error('Подпись к фото — до 900 символов.');
        const payload=new FormData();payload.append('photo',photo);payload.append('body',values.body);payload.append('request_key',state.replyKey);
        result=await api(`/api/admin/orders/${state.thread.order.id}/photo`,{method:'POST',body:payload});
      }else result=await api(`/api/admin/orders/${state.thread.order.id}/messages`,{method:'POST',body:JSON.stringify({body:values.body,request_key:state.replyKey})});
      if(result.message.state==='sent'){form.reset();state.replyKey=key();toast('Ответ доставлен покупателю.');}
      else{error.textContent=deliveryLabels[result.message.state]+'. Проверьте переписку перед повторной отправкой.';error.hidden=false;}
      await refreshThread();
    }else if(form.id==='settings-form'){
      await api('/api/admin/settings',{method:'PATCH',body:JSON.stringify(values)});state.data=await api('/api/bootstrap');await loadAdmin();render();toast('Настройки обновлены в Telegram и на сайте.');
    }else{
      const amount=values.price.replace(',','.');if(!/^\d+(\.\d{1,2})?$/.test(amount))throw new Error('Укажите цену с точностью до копеек.');
      const payload={...values,price:Math.round(Number(amount)*100),max_quantity:Number(values.max_quantity),active:form.elements.active.checked};
      await api('/api/admin/services'+(form.dataset.id?'/'+form.dataset.id:''),{method:form.dataset.id?'PATCH':'POST',body:JSON.stringify(payload)});
      state.busy=false;closeModal();state.data=await api('/api/bootstrap');normalize();await loadAdmin();render();toast('Услуга обновлена в Telegram и на сайте.');
    }
  }catch(e){if(e.status===409&&form.id==='checkout-form'){state.data=await api('/api/bootstrap');normalize();state.busy=false;render();toast(e.message);}else{error.textContent=e.message;error.hidden=false;}}
  finally{state.busy=false;button.disabled=false;}
});
modalRoot.addEventListener('click',e=>{if(e.target.classList.contains('modal-backdrop'))closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();if(e.key==='Tab'&&state.modal){const list=[...modalRoot.querySelectorAll('button:not(:disabled),a[href],input,textarea,select')],first=list[0],last=list.at(-1);if(!first)return;if(e.shiftKey&&(document.activeElement===first||document.activeElement.classList.contains('modal'))){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
tg?.BackButton?.onClick(closeModal);
async function syncSession(){
  if(document.hidden||!state.data?.user)return;
  const previous=state.data.user.id;
  try{const fresh=await api('/api/bootstrap');if(previous&&!fresh.user){state.busy=false;closeModal();state.data=fresh;state.admin=null;state.page='orders';render();toast('Выполнен выход со всех устройств. Войдите заново.');}}catch{}
}
window.addEventListener('focus',syncSession);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncSession();});
async function refresh(){if(document.hidden||state.busy)return;try{if(state.modal==='thread'){await refreshThread();return;}if(state.modal||state.page==='cart'||(state.page==='admin'&&state.adminTab==='settings'))return;if(state.page==='admin'){if(document.activeElement?.id!=='order-search')await loadAdmin();return;}if(state.page==='orders'){await loadOrders();return;}const data=await api('/api/bootstrap');const changed=state.data.user?.id!==data.user?.id||JSON.stringify(state.data.services)!==JSON.stringify(data.services)||state.data.contact_url!==data.contact_url;state.data=data;if(changed){normalize();render();toast('Каталог обновился. Проверьте актуальные цены.');}}catch{}}
async function boot(){try{tg?.ready();tg?.expand();tg?.setHeaderColor('#f7f6f2');tg?.setBackgroundColor('#f7f6f2');state.data=await api('/api/bootstrap');try{state.cart=JSON.parse(sessionStorage.getItem('konspektum.cart')||'{}');if(!state.cart||Array.isArray(state.cart)||typeof state.cart!=='object')state.cart={};}catch{state.cart={};}normalize();const page=location.hash.slice(1);await navigate(['catalog','cart','orders','about','admin'].includes(page)?page:'catalog');state.refreshTimer=setInterval(refresh,10000);setInterval(syncSession,10000);}catch(e){root.innerHTML=`<div class="shell"><div class="error-box"><span class="brand-icon">к.</span><h2>Не открылась страница</h2><p>${h(e.message)}</p><button class="btn" data-action="retry">Попробовать снова</button></div></div>`;}}
boot();
