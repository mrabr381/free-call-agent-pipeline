/* Interactive call simulator — browser port of the Python text simulator.
 *
 * Mirrors agent/simulate.py + agent/dialogue.py + agent/tools.py +
 * agent/store.py, driven by the fictional Bright Smile Dental Clinic
 * config (configs/clinic.yaml). Same rules, same replies, same booking
 * codes (BRI-000001, BRI-000002, ...).
 *
 * Static only: no server, no API keys, no LiveKit, no external requests.
 * The booking store is in-memory, so a page reload starts a fresh demo.
 *
 * The engine is also loadable in Node for testing:
 *   const { createDemo } = require('./app.js');
 *   const demo = createDemo('2027-01-10'); // fixed demo "today"
 *   demo.engine.greeting();
 *   demo.engine.processTurn("Hi, I'd like to book a cleaning appointment");
 *
 * One deliberate, documented deviation from the Python engine: the Python
 * dialogue only enters the booking flow on the words book / appointment /
 * schedule / viewing (or while a booking is already in progress). The
 * browser demo additionally enters it when the caller names a service
 * together with need / want / would like (e.g. "I need a cleaning next
 * week"), so the suggested messages on the page start a booking instead of
 * hitting the FAQ fallback. Everything else — parsing, availability,
 * overlap / double-booking rejection, codes, replies — is a line-for-line
 * port. "Next week" is NOT parsed as a date (Python doesn't parse it
 * either): the agent asks for a date, exactly as the simulator would.
 */
'use strict';

/* ------------------------------------------------------------------ */
/* Config — copied from configs/clinic.yaml (fictional demo client).   */
/* ------------------------------------------------------------------ */
const CLINIC = {
  business: {
    name: 'Bright Smile Dental Clinic',
    type: 'dental clinic',
    phone: '+44 20 7946 0018',
    address: '12 High Street, London NW1 6JH',
    directions: "Two minutes' walk from Camden Town station, opposite the library.",
    currency: 'GBP',
  },
  agent: {
    name: 'Maya',
    greeting: 'Hello, thank you for calling Bright Smile Dental Clinic. This is Maya, how can I help you today?',
    persona: 'Warm, efficient dental receptionist. Speak in short, clear sentences. Always confirm the date, time and doctor before booking. Never give medical advice — offer an appointment instead.',
  },
  hours: {
    monday: { open: '09:00', close: '18:00' },
    tuesday: { open: '09:00', close: '18:00' },
    wednesday: { open: '09:00', close: '18:00' },
    thursday: { open: '09:00', close: '18:00' },
    friday: { open: '09:00', close: '18:00' },
    saturday: { open: '09:00', close: '14:00' },
    sunday: 'closed',
  },
  services: [
    { name: 'Check-up', price: 25.00, duration_min: 30, description: 'Routine examination and oral health check.' },
    { name: 'Cleaning', price: 45.00, duration_min: 45, description: 'Scale and polish with the hygienist.' },
    { name: 'Filling', price: 80.00, duration_min: 60, description: 'White composite filling, one tooth.' },
    { name: 'Whitening', price: 150.00, duration_min: 60, description: 'Professional teeth whitening session.' },
    { name: 'Emergency appointment', price: 60.00, duration_min: 30, description: 'Same-day urgent care for pain or damage.' },
  ],
  staff: [
    { name: 'Dr. Amina Khan', role: 'Principal dentist' },
    { name: 'Dr. Bilal Ahmed', role: 'Associate dentist' },
    { name: 'Dr. Sara Malik', role: 'Hygienist' },
  ],
  faqs: [
    { question: 'Do you take new patients?', answer: 'Yes, we are accepting new patients. A first check-up is £25 and takes 30 minutes — shall I book one for you?' },
    { question: 'Do you offer NHS treatment?', answer: 'We are a private practice, so all treatment is charged at our private rates. A check-up is £25.' },
    { question: 'Is there parking nearby?', answer: 'There is pay-and-display parking on High Street, and the NCP car park is a five-minute walk away.' },
    { question: 'What should I do in a dental emergency?', answer: 'Call us as soon as we open and we will fit you in the same day. Our emergency appointment is £60 for 30 minutes.' },
  ],
};

const DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
/* Demo reference date — the same --today the scripted Python run uses,
   so "tomorrow" and the one-click demo booking match sample_output/. */
const DEMO_TODAY = '2027-01-10';

/* ------------------------------------------------------------------ */
/* Dates / times — UTC dates only, so the demo is identical everywhere. */
/* ------------------------------------------------------------------ */
function toMinutes(hhmm) {
  const parts = hhmm.split(':');
  return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

function toHhmm(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
}

/* Strict YYYY-MM-DD -> Date at UTC midnight, or null (mirrors
   datetime.date.fromisoformat, which rejects e.g. 2027-02-30). */
function parseISODate(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) return null;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (d.getUTCFullYear() !== +m[1] || d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) return null;
  return d;
}

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function addDays(d, n) {
  const c = new Date(d.getTime());
  c.setUTCDate(c.getUTCDate() + n);
  return c;
}

/* Python weekday(): Monday=0 .. Sunday=6. JS getUTCDay(): Sunday=0. */
function pythonWeekday(d) {
  return (d.getUTCDay() + 6) % 7;
}

function dayName(d) {
  return DAY_NAMES[pythonWeekday(d)];
}

function todayISO() {
  const now = new Date();
  return isoDate(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())));
}

/* ------------------------------------------------------------------ */
/* Booking store — port of agent/store.py (in-memory instead of SQLite).*/
/* One rule matters: a resource can never be double-booked. Enforced by */
/* the same [start, end) overlap check the Python store uses.           */
/* ------------------------------------------------------------------ */
class BookingError extends Error {}

class BookingStore {
  constructor() {
    this.rows = [];
    this.nextId = 1;
  }

  _overlaps(resource, date, start, end) {
    const s = toMinutes(start);
    const e = toMinutes(end);
    for (const row of this.rows) {
      if (row.resource !== resource || row.date !== date || row.status !== 'booked') continue;
      const rs = toMinutes(row.start);
      const re = toMinutes(row.end);
      if (s < re && rs < e) return row;
    }
    return null;
  }

  book(opts) {
    const business = opts.business;
    const resource = opts.resource;
    const service = opts.service;
    const date = opts.date;
    const start = opts.start;
    const durationMin = opts.duration_min;
    const customerName = opts.customer_name;
    const customerPhone = opts.customer_phone;
    const today = opts.today; /* Date */
    const day = parseISODate(date);
    if (!day) throw new BookingError('date must be YYYY-MM-DD, got ' + JSON.stringify(date));
    if (day < today) throw new BookingError('cannot book in the past: ' + date + ' (today is ' + isoDate(today) + ')');
    if (!customerName.trim()) throw new BookingError('customer name is required');
    if (!customerPhone.trim()) throw new BookingError('customer phone is required');
    const end = toHhmm(toMinutes(start) + durationMin);
    const clash = this._overlaps(resource, date, start, end);
    if (clash) {
      throw new BookingError(resource + ' is already booked ' + date + ' ' + clash.start + '–' + clash.end +
        ' (' + clash.service + ', ' + clash.code + ')');
    }
    const id = this.nextId++;
    /* Same code rule as store.py: first 3 letters of the business name,
       upper-cased, + "-" + zero-padded row id. "Bright..." -> BRI-000001. */
    const code = business.slice(0, 3).toUpperCase() + '-' + String(id).padStart(6, '0');
    const appt = {
      id: id,
      code: code,
      business: business,
      resource: resource,
      service: service,
      date: date,
      start: start,
      end: end,
      duration_min: durationMin,
      customer_name: customerName.trim(),
      customer_phone: customerPhone.trim(),
      status: 'booked',
      created_at: new Date().toISOString(),
    };
    this.rows.push(appt);
    return Object.assign({}, appt);
  }

  get(code) {
    const want = String(code).trim().toUpperCase();
    const row = this.rows.find(function (r) { return r.code === want; });
    if (!row) throw new BookingError('no appointment with code ' + JSON.stringify(code));
    return Object.assign({}, row);
  }

  listFor(opts) {
    opts = opts || {};
    const status = opts.status || 'booked';
    let out = this.rows.filter(function (r) { return r.status === status; });
    if (opts.resource) out = out.filter(function (r) { return r.resource === opts.resource; });
    if (opts.date) out = out.filter(function (r) { return r.date === opts.date; });
    out.sort(function (a, b) {
      return (a.date + a.start).localeCompare(b.date + b.start);
    });
    return out.map(function (r) { return Object.assign({}, r); });
  }

  freeSlots(opts) {
    const resource = opts.resource;
    const date = opts.date;
    const durationMin = opts.duration_min;
    const stepMin = opts.step_min || 30;
    const booked = this.listFor({ resource: resource, date: date });
    const taken = booked.map(function (b) { return [toMinutes(b.start), toMinutes(b.end)]; });
    const free = [];
    let start = toMinutes(opts.open_hhmm);
    const lastStart = toMinutes(opts.close_hhmm) - durationMin;
    while (start <= lastStart) {
      const end = start + durationMin;
      const clashes = taken.some(function (pair) { return start < pair[1] && pair[0] < end; });
      if (!clashes) free.push(toHhmm(start));
      start += stepMin;
    }
    return free;
  }

  cancel(code) {
    const appt = this.get(code);
    if (appt.status === 'cancelled') throw new BookingError('appointment ' + appt.code + ' is already cancelled');
    const row = this.rows.find(function (r) { return r.code === appt.code; });
    row.status = 'cancelled';
    return Object.assign({}, row);
  }

  reschedule(code, opts) {
    const appt = this.get(code);
    if (appt.status === 'cancelled') throw new BookingError('cannot reschedule cancelled appointment ' + appt.code);
    const today = opts.today;
    const day = parseISODate(opts.new_date);
    if (!day) throw new BookingError('date must be YYYY-MM-DD, got ' + JSON.stringify(opts.new_date));
    if (day < today) throw new BookingError('cannot move to the past: ' + opts.new_date + ' (today is ' + isoDate(today) + ')');
    const newEnd = toHhmm(toMinutes(opts.new_start) + appt.duration_min);
    const clash = this._overlaps(appt.resource, opts.new_date, opts.new_start, newEnd);
    if (clash && clash.code !== appt.code) {
      throw new BookingError(appt.resource + ' is already booked ' + opts.new_date + ' ' +
        clash.start + '–' + clash.end + ' (' + clash.code + ')');
    }
    const row = this.rows.find(function (r) { return r.code === appt.code; });
    row.date = opts.new_date;
    row.start = opts.new_start;
    row.end = newEnd;
    return Object.assign({}, row);
  }
}

/* ------------------------------------------------------------------ */
/* Agent tools — port of agent/tools.py. Every tool returns a plain     */
/* dict with ok plus either data or error; a bad input becomes a clear  */
/* error message, never an exception into the caller.                   */
/* ------------------------------------------------------------------ */
class AgentTools {
  constructor(config, store) {
    this.cfg = config;
    this.store = store;
    this.business = config.business.name;
    this._messages = [];
  }

  _service(name) {
    const needle = String(name).trim().toLowerCase();
    for (const svc of this.cfg.services) {
      if (svc.name.trim().toLowerCase() === needle) return svc;
      /* Python tools.py: `if name in svc["name"]` — the input may be a
         fragment of the service name ("clean" matches "Cleaning"). */
      if (needle && svc.name.trim().toLowerCase().indexOf(needle) !== -1) return svc;
    }
    return null;
  }

  _staff(name) {
    const needle = String(name).trim().toLowerCase();
    for (const person of this.cfg.staff) {
      if (person.name.trim().toLowerCase() === needle) return person;
      if (needle && person.name.trim().toLowerCase().indexOf(needle) !== -1) return person;
    }
    return null;
  }

  _dayHours(date) {
    const day = parseISODate(date);
    if (!day) throw new Error('date must be YYYY-MM-DD, got ' + JSON.stringify(date));
    const hours = this.cfg.hours[dayName(day)];
    return hours === 'closed' ? null : hours;
  }

  getHours() {
    const lines = DAY_NAMES.map(function (day) {
      const val = this.cfg.hours[day];
      const label = day.charAt(0).toUpperCase() + day.slice(1);
      return val === 'closed' ? label + ': closed' : label + ': ' + val.open + '–' + val.close;
    }, this);
    return { ok: true, hours: lines };
  }

  getServicesAndPrices() {
    const currency = this.cfg.business.currency || 'GBP';
    const services = this.cfg.services.map(function (s) {
      return { name: s.name, price: s.price, currency: currency, duration_min: s.duration_min, description: s.description || '' };
    });
    return { ok: true, services: services };
  }

  getDirections() {
    const b = this.cfg.business;
    return { ok: true, address: b.address, phone: b.phone, directions: b.directions || '' };
  }

  checkAvailability(opts) {
    const svc = this._service(opts.service);
    if (!svc) {
      const known = this.cfg.services.map(function (s) { return s.name; }).join(', ');
      return { ok: false, error: "we don't offer " + JSON.stringify(opts.service) + '. Our services are: ' + known + '.' };
    }
    let hours;
    try {
      hours = this._dayHours(opts.date);
    } catch (e) {
      return { ok: false, error: 'date must be YYYY-MM-DD, got ' + JSON.stringify(opts.date) + '.' };
    }
    if (hours === null) {
      const day = parseISODate(opts.date);
      const weekday = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][day.getUTCDay()];
      return { ok: false, error: 'we are closed on ' + weekday + 's (' + opts.date + ').' };
    }
    let people = null;
    if (opts.staff) {
      const person = this._staff(opts.staff);
      if (!person) {
        const known = this.cfg.staff.map(function (p) { return p.name; }).join(', ');
        return { ok: false, error: "we don't have " + JSON.stringify(opts.staff) + ' on the team. Our team: ' + known + '.' };
      }
      people = [person];
    } else {
      people = this.cfg.staff.slice();
    }
    const slots = {};
    for (const person of people) {
      const free = this.store.freeSlots({
        resource: person.name, date: opts.date,
        open_hhmm: hours.open, close_hhmm: hours.close, duration_min: svc.duration_min,
      });
      if (free.length) slots[person.name] = free;
    }
    if (!Object.keys(slots).length) {
      return { ok: false, error: 'no free slots for ' + svc.name + ' on ' + opts.date + '.' };
    }
    return { ok: true, service: svc.name, date: opts.date, slots: slots };
  }

  bookAppointment(opts) {
    const svc = this._service(opts.service);
    if (!svc) {
      const known = this.cfg.services.map(function (s) { return s.name; }).join(', ');
      return { ok: false, error: "we don't offer " + JSON.stringify(opts.service) + '. Our services are: ' + known + '.' };
    }
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(opts.time || '')) {
      return { ok: false, error: 'time must be HH:MM, got ' + JSON.stringify(opts.time) + '.' };
    }
    let hours;
    try {
      hours = this._dayHours(opts.date);
    } catch (e) {
      return { ok: false, error: 'date must be YYYY-MM-DD, got ' + JSON.stringify(opts.date) + '.' };
    }
    if (hours === null) {
      return { ok: false, error: 'we are closed on ' + opts.date + '.' };
    }
    let people = null;
    if (opts.staff) {
      const person = this._staff(opts.staff);
      if (!person) {
        const known = this.cfg.staff.map(function (p) { return p.name; }).join(', ');
        return { ok: false, error: "we don't have " + JSON.stringify(opts.staff) + ' on the team. Our team: ' + known + '.' };
      }
      people = [person];
    } else {
      people = this.cfg.staff.slice();
    }
    /* Pick the first person free at the requested time; "any" is fine. */
    for (const person of people) {
      const free = this.store.freeSlots({
        resource: person.name, date: opts.date,
        open_hhmm: hours.open, close_hhmm: hours.close, duration_min: svc.duration_min,
      });
      if (free.indexOf(opts.time) !== -1) {
        try {
          const appt = this.store.book({
            business: this.business, resource: person.name,
            service: svc.name, date: opts.date, start: opts.time,
            duration_min: svc.duration_min,
            customer_name: opts.customer_name, customer_phone: opts.customer_phone,
            today: opts.today,
          });
          return { ok: true, appointment: appt };
        } catch (exc) {
          if (exc instanceof BookingError) return { ok: false, error: exc.message };
          throw exc;
        }
      }
    }
    return {
      ok: false,
      error: 'no free slot at ' + opts.time + ' on ' + opts.date + ' for ' + svc.name + '. Ask me to check availability for other times.',
    };
  }

  cancelAppointment(code) {
    try {
      const appt = this.store.cancel(code);
      return { ok: true, appointment: appt };
    } catch (exc) {
      if (exc instanceof BookingError) return { ok: false, error: exc.message };
      throw exc;
    }
  }

  rescheduleAppointment(code, opts) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(opts.new_time || '')) {
      return { ok: false, error: 'time must be HH:MM, got ' + JSON.stringify(opts.new_time) + '.' };
    }
    try {
      const appt = this.store.reschedule(code, { new_date: opts.new_date, new_start: opts.new_time, today: opts.today });
      return { ok: true, appointment: appt };
    } catch (exc) {
      if (exc instanceof BookingError) return { ok: false, error: exc.message };
      throw exc;
    }
  }

  takeMessage(opts) {
    if (!opts.message.trim()) {
      return { ok: false, error: 'I need the message itself to pass it on.' };
    }
    const record = {
      customer_name: opts.customer_name.trim(),
      customer_phone: opts.customer_phone.trim(),
      message: opts.message.trim(),
      taken_at: new Date().toISOString(),
    };
    this._messages.push(record);
    return { ok: true, message: record };
  }

  answerFaq(question) {
    const q = question.toLowerCase();
    let best = null;
    let bestScore = 0;
    for (const faq of this.cfg.faqs || []) {
      const words = (faq.question.toLowerCase().match(/[a-z0-9]+/g) || []);
      let score = 0;
      for (const w of words) {
        if (w.length > 3 && q.indexOf(w) !== -1) score++;
      }
      if (score > bestScore) {
        bestScore = score;
        best = faq;
      }
    }
    if (!best || bestScore === 0) {
      return { ok: false, error: "I don't have an answer for that — would you like me to take a message for the team?" };
    }
    return { ok: true, question: best.question, answer: best.answer };
  }
}

/* ------------------------------------------------------------------ */
/* Dialogue engine — port of agent/dialogue.py. Deterministic: given   */
/* the same script and the same reference date, the transcript is the   */
/* same every time.                                                     */
/* ------------------------------------------------------------------ */
const DATE_RE = /\b(20\d\d-\d\d-\d\d)\b/;
const TIME_RE = /\b([01]?\d):([0-5]\d)\s*(am|pm)?\b/i;
const CODE_RE = /\b([A-Z]{2,4}-\d{6})\b/;
const PHONE_RE = /\+?\d[\d\s\-]{6,}\d/;
const NAME_RE = /\b(?:my name is|i'?m|this is|name[' ]s)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)/i;

function wordTimeToHhmm(hour, minute, ampm) {
  if (ampm) {
    ampm = ampm.toLowerCase();
    if (ampm === 'pm' && hour < 12) hour += 12;
    if (ampm === 'am' && hour === 12) hour = 0;
  }
  if (hour < 0 || hour > 23) return null;
  return String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0');
}

class DialogueEngine {
  /* today: 'YYYY-MM-DD' reference date (demo default DEMO_TODAY). */
  constructor(config, tools, today) {
    this.cfg = config;
    this.tools = tools;
    this.today = parseISODate(today || todayISO());
    this.pending = {}; /* partial booking being collected */
    this.lastCode = null;
    this.callerName = '';
    this.callerPhone = '';
  }

  _parseDate(text) {
    const m = DATE_RE.exec(text);
    if (m) return m[1];
    const low = text.toLowerCase();
    if (low.indexOf('tomorrow') !== -1) return isoDate(addDays(this.today, 1));
    if (low.indexOf('today') !== -1) return isoDate(this.today);
    if (low.indexOf('next monday') !== -1) return this._nextWeekday(0);
    if (low.indexOf('next friday') !== -1) return this._nextWeekday(4);
    return null;
  }

  _nextWeekday(weekday) {
    /* Python: delta = (weekday - today.weekday()) % 7 or 7 */
    let delta = (weekday - pythonWeekday(this.today)) % 7;
    if (delta === 0) delta = 7;
    return isoDate(addDays(this.today, delta));
  }

  _parseTime(text) {
    const m = TIME_RE.exec(text);
    if (!m) return null;
    return wordTimeToHhmm(parseInt(m[1], 10), parseInt(m[2], 10), m[3] || null);
  }

  _parseService(text) {
    const low = text.toLowerCase();
    /* Pass 1: exact service name in the text. */
    for (const svc of this.cfg.services) {
      if (low.indexOf(svc.name.toLowerCase()) !== -1) return svc.name;
    }
    /* Pass 2: score by how many of the service's words appear as whole
       words in the text. Highest score wins, and only wins outright — a
       tie is ambiguous and returns null so the agent asks which one. */
    const wordsInText = new Set(low.match(/[a-z]+/g) || []);
    const scored = this.cfg.services.map(function (svc) {
      const svcWords = new Set(svc.name.toLowerCase().match(/[a-z]+/g) || []);
      let score = 0;
      svcWords.forEach(function (w) { if (wordsInText.has(w)) score++; });
      return [score, svc.name];
    });
    scored.sort(function (a, b) { return b[0] - a[0]; });
    if (scored.length && scored[0][0] > 0 && (scored.length === 1 || scored[0][0] > scored[1][0])) {
      return scored[0][1];
    }
    return null;
  }

  _parseStaff(text) {
    const low = text.toLowerCase();
    for (const person of this.cfg.staff) {
      const name = person.name.toLowerCase();
      if (low.indexOf(name) !== -1) return person.name;
      /* Name without the honorific ("amina khan"), then given name alone. */
      const bare = name.replace(/^(dr|mr|mrs|ms|miss)\.\s+/, '');
      if (low.indexOf(bare) !== -1) return person.name;
      const first = bare.split(' ')[0];
      if (new RegExp('\\b' + first.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b').test(low)) {
        return person.name;
      }
    }
    return null;
  }

  _harvestIdentity(text) {
    const m = NAME_RE.exec(text);
    if (m && !this.callerName) this.callerName = m[1].trim();
    /* Dates look like phone numbers to a naive regex — hide them first. */
    const scrubbed = text.replace(/\b20\d\d-\d\d-\d\d\b/g, ' ');
    const pm = PHONE_RE.exec(scrubbed);
    if (pm && !this.callerPhone) {
      const digits = pm[0].replace(/\D/g, '');
      if (digits.length >= 7 && digits.length <= 15) this.callerPhone = digits;
    }
  }

  greeting() {
    return this.cfg.agent.greeting;
  }

  /* One caller turn in -> { reply, toolCalls } out. */
  processTurn(text) {
    const toolCalls = [];
    this._harvestIdentity(text);
    const low = text.toLowerCase().trim();

    /* --- cancel ---------------------------------------------------- */
    if (low.indexOf('cancel') !== -1) {
      const code = this._code(text) || this.lastCode;
      if (!code) {
        return { reply: 'Of course — could you give me your booking reference code? It looks like ABC-000123.', toolCalls: toolCalls };
      }
      const result = this._call(toolCalls, 'cancel_appointment', this.tools.cancelAppointment.bind(this.tools), code);
      if (result.ok) {
        const a = result.appointment;
        return { reply: 'Done — your ' + a.service + ' on ' + a.date + ' at ' + a.start + ' with ' + a.resource + ' is cancelled. The slot is free again, so just call back whenever you\'d like to rebook.', toolCalls: toolCalls };
      }
      return { reply: "I'm sorry, " + result.error + ' Could you check the code and try again?', toolCalls: toolCalls };
    }

    /* --- reschedule ------------------------------------------------ */
    if (low.indexOf('reschedule') !== -1 || low.indexOf('move my') !== -1 || low.indexOf('change my appointment') !== -1) {
      const code = this._code(text) || this.lastCode;
      const date = this._parseDate(text);
      const time = this._parseTime(text);
      if (!code) {
        return { reply: 'Sure — could you give me your booking reference code first?', toolCalls: toolCalls };
      }
      if (!date || !time) {
        return { reply: 'Sure — which new date and time would you like? For example: move it to 2027-01-16 at 11:00.', toolCalls: toolCalls };
      }
      const result = this._call(toolCalls, 'reschedule_appointment', this.tools.rescheduleAppointment.bind(this.tools),
        code, { new_date: date, new_time: time, today: this.today });
      if (result.ok) {
        const a = result.appointment;
        return { reply: 'Done — your appointment is moved to ' + a.date + ' at ' + a.start + ' with ' + a.resource + '. Your reference code stays ' + a.code + '.', toolCalls: toolCalls };
      }
      return { reply: "I'm sorry, " + result.error, toolCalls: toolCalls };
    }

    /* --- booking (new or continuing) ------------------------------- */
    const wantsBooking = Object.keys(this.pending).length > 0 ||
      ['book', 'appointment', 'schedule', 'viewing'].some(function (w) { return low.indexOf(w) !== -1; }) ||
      /* Demo-only extension (see header): "I need a cleaning ..." */
      (/\b(need|want)\b/.test(low) || low.indexOf('would like') !== -1) && this._parseService(text) !== null;
    if (wantsBooking) {
      return this._bookingTurn(text, toolCalls);
    }

    /* --- hours ----------------------------------------------------- */
    if (['hours', 'open', 'timing', 'when do you close', 'when are you open'].some(function (w) { return low.indexOf(w) !== -1; })) {
      const result = this._call(toolCalls, 'get_hours', this.tools.getHours.bind(this.tools));
      const lines = result.hours.map(function (h) { return '  ' + h; }).join('\n');
      return { reply: 'Our opening hours are:\n' + lines, toolCalls: toolCalls };
    }

    /* --- services / prices ----------------------------------------- */
    if (['service', 'price', 'cost', 'how much', 'what do you offer', 'treatments', 'listings'].some(function (w) { return low.indexOf(w) !== -1; })) {
      const result = this._call(toolCalls, 'get_services_and_prices', this.tools.getServicesAndPrices.bind(this.tools));
      const lines = result.services.map(function (s) {
        return '  ' + s.name + ': ' + s.currency + ' ' + s.price.toFixed(2) + ' (' + s.duration_min + ' min)' +
          (s.description ? ' — ' + s.description : '');
      });
      return { reply: 'Here is what we offer:\n' + lines.join('\n'), toolCalls: toolCalls };
    }

    /* --- directions ------------------------------------------------ */
    if (['where are you', 'address', 'directions', 'location', 'how do i get', 'find you'].some(function (w) { return low.indexOf(w) !== -1; })) {
      const result = this._call(toolCalls, 'get_directions', this.tools.getDirections.bind(this.tools));
      const extra = result.directions ? ' ' + result.directions : '';
      return { reply: "We're at " + result.address + '.' + extra + ' Our phone number is ' + result.phone + '.', toolCalls: toolCalls };
    }

    /* --- human / message ------------------------------------------- */
    if (['speak to a human', 'speak to someone', 'real person', 'call me back', 'leave a message', 'take a message'].some(function (w) { return low.indexOf(w) !== -1; })) {
      if (!this.callerName || !this.callerPhone) {
        return { reply: 'Of course — could you give me your name and phone number, and the message you\'d like passed on?', toolCalls: toolCalls };
      }
      const result = this._call(toolCalls, 'take_message', this.tools.takeMessage.bind(this.tools),
        { customer_name: this.callerName, customer_phone: this.callerPhone, message: text });
      if (result.ok) {
        return { reply: 'Thank you ' + this.callerName + " — I've left your message for the team and someone will call you back on " + this.callerPhone + '. Is there anything else I can help with?', toolCalls: toolCalls };
      }
      return { reply: result.error, toolCalls: toolCalls };
    }

    /* --- FAQ / fallback -------------------------------------------- */
    const result = this._call(toolCalls, 'answer_faq', this.tools.answerFaq.bind(this.tools), text);
    if (result.ok) {
      return { reply: result.answer, toolCalls: toolCalls };
    }
    return { reply: "I'm sorry, I don't have an answer for that. I can help with bookings, opening hours, services and prices, or directions — or I can take a message for the team. What would you like?", toolCalls: toolCalls };
  }

  _bookingTurn(text, toolCalls) {
    const p = this.pending;
    const parsers = { service: this._parseService.bind(this), staff: this._parseStaff.bind(this), date: this._parseDate.bind(this), time: this._parseTime.bind(this) };
    for (const key of ['service', 'staff', 'date', 'time']) {
      if (!(key in p)) {
        const val = parsers[key](text);
        if (val) p[key] = val;
      }
    }
    if (!('service' in p)) {
      const known = this.cfg.services.map(function (s) { return s.name; }).join(', ');
      return { reply: 'Happy to help with a booking. Which service would you like? We offer: ' + known + '.', toolCalls: toolCalls };
    }
    if (!('date' in p)) {
      return { reply: 'Which date works for you? You can say a date like 2027-01-15, or just say tomorrow.', toolCalls: toolCalls };
    }
    if (!('time' in p)) {
      const result = this._call(toolCalls, 'check_availability', this.tools.checkAvailability.bind(this.tools),
        { service: p.service, date: p.date, staff: p.staff || '' });
      if (!result.ok) {
        this.pending = {};
        return { reply: "I'm sorry, " + result.error, toolCalls: toolCalls };
      }
      const options = [];
      for (const person of Object.keys(result.slots)) {
        options.push(person + ': ' + result.slots[person].slice(0, 6).join(', '));
      }
      return { reply: 'Here are the free times on ' + p.date + ' for ' + p.service + ':\n  ' + options.join('\n  ') + '\nWhich time would you like?', toolCalls: toolCalls };
    }
    if (!this.callerName || !this.callerPhone) {
      return { reply: 'Nearly there — could I have your name and phone number to confirm the booking?', toolCalls: toolCalls };
    }
    const result = this._call(toolCalls, 'book_appointment', this.tools.bookAppointment.bind(this.tools),
      { service: p.service, date: p.date, time: p.time, staff: p.staff || '', customer_name: this.callerName, customer_phone: this.callerPhone, today: this.today });
    this.pending = {};
    if (!result.ok) {
      return { reply: "I'm sorry, " + result.error, toolCalls: toolCalls };
    }
    const a = result.appointment;
    this.lastCode = a.code;
    return { reply: "You're booked in, " + a.customer_name + '! ' + a.service + ' with ' + a.resource + ' on ' + a.date + ' at ' + a.start + '–' + a.end + '. Your reference code is ' + a.code + ' — please keep it handy. We\'ll see you then!', toolCalls: toolCalls };
  }

  _code(text) {
    const m = CODE_RE.exec(text.toUpperCase());
    return m ? m[1] : null;
  }

  _call(toolCalls, name, fn) {
    const args = Array.prototype.slice.call(arguments, 3);
    const result = fn.apply(null, args);
    toolCalls.push({ tool: name, args: args, result: result });
    return result;
  }
}

/* One fresh demo session: store + tools + engine sharing one "today". */
function createDemo(today) {
  const store = new BookingStore();
  const tools = new AgentTools(CLINIC, store);
  const engine = new DialogueEngine(CLINIC, tools, today || DEMO_TODAY);
  return { config: CLINIC, store: store, tools: tools, engine: engine, today: isoDate(engine.today) };
}

/* Node export (browser: plain <script>, everything is global). */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CLINIC: CLINIC, DEMO_TODAY: DEMO_TODAY, BookingStore: BookingStore, BookingError: BookingError, AgentTools: AgentTools, DialogueEngine: DialogueEngine, createDemo: createDemo, toMinutes: toMinutes, toHhmm: toHhmm, parseISODate: parseISODate, isoDate: isoDate };
}

/* ------------------------------------------------------------------ */
/* Browser UI — only runs when document exists (not in Node).          */
/* ------------------------------------------------------------------ */
if (typeof document !== 'undefined') {
  (function () {
    var demo = createDemo(DEMO_TODAY);
    var chatEl = document.getElementById('chat');
    var inputEl = document.getElementById('caller-input');
    var sendBtn = document.getElementById('send-btn');
    var runBtn = document.getElementById('run-demo-btn');
    var resetBtn = document.getElementById('reset-btn');
    var calendarEl = document.getElementById('calendar');
    var bookingsEl = document.getElementById('bookings');
    var recordEl = document.getElementById('booking-record');
    var running = false;

    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function addBubble(who, text, toolCalls) {
      var div = document.createElement('div');
      div.className = 'bubble ' + (who === 'agent' ? 'agent' : 'caller');
      var label = who === 'agent' ? 'Maya (receptionist)' : 'You (caller)';
      var html = '<strong>' + label + ':</strong> ' + esc(text).replace(/\n/g, '<br>');
      if (toolCalls && toolCalls.length) {
        html += toolCalls.map(function (c) {
          return '<div class="tool">[tool: ' + esc(c.tool) + ' &rarr; ok=' + (c.result.ok ? 'true' : 'false') + ']</div>';
        }).join('');
      }
      div.innerHTML = html;
      chatEl.appendChild(div);
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    function renderCalendar() {
      var today = parseISODate(demo.today);
      var weekdayLabels = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
      var html = '';
      for (var i = 0; i < 7; i++) {
        var d = addDays(today, i);
        var dateStr = isoDate(d);
        var hours = CLINIC.hours[dayName(d)];
        var hoursText = hours === 'closed' ? 'Closed' : hours.open + '–' + hours.close;
        var booked = demo.store.listFor({ date: dateStr });
        var chips = booked.map(function (b) {
          return '<span class="appt">' + esc(b.start) + '–' + esc(b.end) + ' · ' + esc(b.service) + ' · ' + esc(b.resource) + ' · ' + esc(b.code) + '</span>';
        }).join('');
        html += '<div class="day' + (dateStr === '2027-01-15' ? ' highlight' : '') + '">' +
          '<strong>' + weekdayLabels[d.getUTCDay()] + ' ' + dateStr + '</strong>' +
          '<span class="hours">' + esc(hoursText) + '</span>' +
          (chips || '<span class="free">No bookings yet</span>') +
          '</div>';
      }
      calendarEl.innerHTML = html;
    }

    function renderBookings() {
      var all = demo.store.listFor({ status: 'booked' });
      var cancelled = demo.store.listFor({ status: 'cancelled' });
      if (!all.length && !cancelled.length) {
        bookingsEl.innerHTML = '<p class="muted">No bookings yet — book one in the chat and it appears here.</p>';
      } else {
        var rows = all.concat(cancelled).map(function (b) {
          return '<tr><td>' + esc(b.code) + '</td><td>' + esc(b.service) + '</td><td>' + esc(b.resource) + '</td><td>' + esc(b.date) + ' ' + esc(b.start) + '–' + esc(b.end) + '</td><td>' + esc(b.customer_name) + '</td><td>' + esc(b.status) + '</td></tr>';
        }).join('');
        bookingsEl.innerHTML = '<table><tr><th>Code</th><th>Service</th><th>Doctor</th><th>Date / time</th><th>Caller</th><th>Status</th></tr>' + rows + '</table>';
      }
      var latest = demo.store.listFor({ status: 'booked' }).slice(-1)[0];
      if (latest) {
        recordEl.innerHTML =
          '<dl>' +
          '<dt>Service</dt><dd>' + esc(latest.service) + '</dd>' +
          '<dt>Doctor</dt><dd>' + esc(latest.resource) + '</dd>' +
          '<dt>Date / time</dt><dd>' + esc(latest.date) + ' ' + esc(latest.start) + '–' + esc(latest.end) + '</dd>' +
          '<dt>Caller</dt><dd>' + esc(latest.customer_name) + ' (' + esc(latest.customer_phone) + ')</dd>' +
          '<dt>Reference code</dt><dd><strong>' + esc(latest.code) + '</strong></dd>' +
          '<dt>Status</dt><dd>' + esc(latest.status) + '</dd>' +
          '</dl>' +
          '<pre>' + esc(JSON.stringify(latest, null, 2)) + '</pre>';
      } else {
        recordEl.innerHTML = '<p class="muted">No booking yet. Run the demo booking or book one yourself — the record appears here, exactly like the Python simulator prints it.</p>';
      }
      renderCalendar();
    }

    function send(text, fromScript) {
      text = (text || '').trim();
      if (!text || (running && !fromScript)) return;
      addBubble('caller', text, []);
      var out = demo.engine.processTurn(text);
      addBubble('agent', out.reply, out.toolCalls);
      renderBookings();
    }

    function greet() {
      addBubble('agent', demo.engine.greeting(), []);
    }

    /* Suggested messages + one-click demo script (sample_data/call1_book.txt). */
    var DEMO_SCRIPT = [
      "Hi, I'd like to book a cleaning appointment",
      'With Dr. Amina on 2027-01-15 at 10:00',
      'My name is Ayesha Khan, my phone is 07123 456789',
    ];

    function runDemoScript() {
      if (running) return;
      running = true;
      runBtn.disabled = true;
      inputEl.disabled = true;
      var i = 0;
      (function step() {
        if (i >= DEMO_SCRIPT.length) {
          running = false;
          runBtn.disabled = false;
          inputEl.disabled = false;
          inputEl.focus();
          return;
        }
        send(DEMO_SCRIPT[i], true);
        i++;
        setTimeout(step, 900);
      })();
    }

    function resetDemo() {
      if (running) return;
      demo = createDemo(DEMO_TODAY);
      chatEl.innerHTML = '';
      greet();
      renderBookings();
      inputEl.focus();
    }

    sendBtn.addEventListener('click', function () { send(inputEl.value); inputEl.value = ''; inputEl.focus(); });
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { send(inputEl.value); inputEl.value = ''; }
    });
    runBtn.addEventListener('click', runDemoScript);
    resetBtn.addEventListener('click', resetDemo);
    document.querySelectorAll('[data-say]').forEach(function (chip) {
      chip.addEventListener('click', function () { send(chip.getAttribute('data-say')); inputEl.focus(); });
    });

    greet();
    renderBookings();
  })();
}
