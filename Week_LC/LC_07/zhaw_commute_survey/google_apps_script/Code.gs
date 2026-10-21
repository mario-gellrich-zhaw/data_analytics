// Restricts the script's access to this one spreadsheet
/** @OnlyCurrentDoc */

/**
 * ZHAW commute survey: central storage of all responses in this Google Sheet.
 *
 * Setup (once, by the lecturer):
 *   Sheet > Extensions > Apps Script > paste this file > Save
 *   Deploy > New deployment > Web app
 *     Execute as:     Me
 *     Who has access: Anyone
 *   Copy the web app URL (ends with /exec) and give it to the students.
 *
 * The URL is the only thing students need (no Google account, no sharing rights):
 *   POST <url>             JSON with the answers -> appends one row
 *   GET  <url>             all responses as JSON  -> used by the app
 *   GET  <url>?format=csv  all responses as CSV   -> used by the notebooks
 */

const SHEET_NAME = 'responses';
const SURVEY_OPEN = true;  // set to false and redeploy to stop accepting responses

// Must match the question keys in questions.py
const TEXT_COLUMNS = ['campus', 'transport', 'travel_pass', 'gender', 'living'];
const NUMBER_COLUMNS = ['door_to_door_min', 'distance_km', 'days_per_week', 'monthly_cost_chf',
                        'satisfied', 'stressful', 'productive', 'age'];
const COLUMNS = ['id', 'created_at', 'campus', 'transport', 'door_to_door_min', 'distance_km',
                 'days_per_week', 'monthly_cost_chf', 'travel_pass', 'satisfied', 'stressful',
                 'productive', 'gender', 'age', 'living'];

function getSheet_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = spreadsheet.getSheetByName(SHEET_NAME) || spreadsheet.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(COLUMNS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function json_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

// The link is open to everyone, so only accept short texts and whole numbers.
// A leading apostrophe stops texts like "=..." from being read as spreadsheet formulas.
function clean_(answers) {
  const row = {};
  for (const key of TEXT_COLUMNS) {
    const value = String(answers[key] === undefined ? '' : answers[key]).trim().slice(0, 60);
    if (!value) throw new Error('Missing answer: ' + key);
    row[key] = /^[=+\-@]/.test(value) ? "'" + value : value;
  }
  for (const key of NUMBER_COLUMNS) {
    const value = Number(answers[key]);
    if (!Number.isInteger(value) || value < 0 || value > 2000) throw new Error('Invalid number: ' + key);
    row[key] = value;
  }
  return row;
}

function doPost(e) {
  if (!SURVEY_OPEN) return json_({ ok: false, error: 'The survey is closed.' });
  let row;
  try {
    row = clean_(JSON.parse(e.postData.contents));
  } catch (err) {
    return json_({ ok: false, error: String(err.message || err) });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);  // many students submit at the same time
  try {
    const sheet = getSheet_();
    const id = sheet.getLastRow();  // row 1 is the header
    sheet.appendRow([id, new Date().toISOString()].concat(COLUMNS.slice(2).map(key => row[key])));
    return json_({ ok: true, id: id });
  } finally {
    lock.releaseLock();
  }
}

function doGet(e) {
  const values = getSheet_().getDataRange().getValues()
    .map(row => row.map(value => value instanceof Date ? value.toISOString() : value));
  const header = values.shift();
  if (e && e.parameter && e.parameter.format === 'csv') {
    const csv = [header].concat(values).map(row => row.map(csvCell_).join(',')).join('\n');
    return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.CSV);
  }
  const rows = values.map(row => Object.fromEntries(header.map((key, i) => [key, row[i]])));
  return json_({ rows: rows });
}

function csvCell_(value) {
  const text = String(value);
  return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
