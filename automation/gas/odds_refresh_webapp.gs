/**
 * dsk_Project
 * 「操作パネル」シートの「②オッズ取得・予想更新」チェックボックス(B3)を
 * Webからリモートで ON にするためのGoogle Apps Script Webアプリ。
 * あわせて、そのジョブの現在のステータス（C3）・完了時刻（E3）、および
 * オッズ自動更新の状態フラグ（B5・C5）を返す状態確認エンドポイントを提供する。
 *
 * アプリ（GitHub Pages、docs/index.html）から fetch() で呼び出される想定。
 * watcher.py（Task Scheduler、5分おき実行）がB3のチェックを検知して
 * automation/odds_refresh_job.py を実行する。
 *
 * オッズ自動更新の状態フラグ（B5）について:
 *   B3（1回きりの実行トリガー、watcher.pyが拾って自動でOFFに戻す）とは別物。
 *   B5は「開催日の間、5分おきに自動更新し続けているかどうか」を示す状態フラグで、
 *   automation/odds_auto_refresh_job.py（Task Schedulerから直接、開催日
 *   9:30〜17:00に5分おきで起動される、watcher.pyのポーリングを経由しない
 *   独立ジョブ）が完全自動でON/OFFを読み書きする（ユーザー操作は無い）。
 *   このWebアプリ側はB5を読み取って状態表示するのみで、B5に書き込む
 *   エンドポイントは提供しない。
 *
 * エンドポイント（PROJECT_EVのautomation/gas/odds_refresh_webapp.gsと同じ設計）:
 *   ?key=SECRET_KEY                            オッズ取得・予想更新リクエスト（B3をtrueにする）
 *   ?key=SECRET_KEY&action=status              同ジョブの現在のステータス確認（読み取りのみ）
 *     戻り値の例: {"status":"実行中"}
 *                 {"status":"完了","completedAt":"2026-09-05 09:35:00"}
 *   ?key=SECRET_KEY&action=autorefresh_status  オッズ自動更新の現在の状態を確認（読み取りのみ）
 *     戻り値の例: {"status":"ok","autoRefreshOn":true,"lastUpdatedAt":"2026-09-05 10:35:00"}
 *
 * 認証について:
 *   デプロイは「アクセスできるユーザー: 全員」だが、誰でもURLを知っていれば
 *   叩けてしまうとジョブが無駄に走る可能性があるため、簡単な合言葉パラメータ
 *   （?key=...）で最低限のチェックを行う。一致しない場合は何もせずエラーを返す。
 *
 * デプロイ方法（スプレッドシートを開いた状態のGoogleアカウントで行う。
 * サービスアカウントではなくユーザー自身のGoogleアカウントでの操作が必要）:
 *   1. スプレッドシート「dsk_Project」を開く →「拡張機能」→「Apps Script」
 *   2. 既存の Code.gs の中身をすべて削除し、このファイルの内容を貼り付ける
 *   3. 上部の「デプロイ」→「新しいデプロイ」→ 歯車アイコン → 種類「ウェブアプリ」→
 *      「次のユーザーとして実行」: 自分（Me）→「アクセスできるユーザー」: 全員（Anyone）
 *      →「デプロイ」（初回は権限承認が必要）
 *   4. 発行された「ウェブアプリ」のURL（.../exec で終わるもの）をdocs/index.htmlの
 *      GAS_WEBAPP_URLへ設定する（末尾に"?key="+SECRET_KEYを付ける必要はなく、
 *      呼び出し側のJSがkeyパラメータを付けて呼ぶ設計にしている）
 *
 * 注意: シート名・セル位置を変更した場合は、automation/sheet_control_panel.py の
 * JOBS定義とあわせてこちらの定数も更新すること。SECRET_KEYを変更した場合は、
 * 呼び出し側（docs/index.html）のURLも必ず合わせて更新すること。
 */

var SPREADSHEET_ID = "1CtHs765uaLP-E2BnY-CWgDAaR3VVoKt_yiVYaInm0Fs";
var SHEET_NAME = "操作パネル";
var CHECKBOX_CELL = "B3";     // 「②オッズ取得・予想更新」の実行チェック
var STATUS_CELL = "C3";       // 同ジョブのステータス（待機中/実行中/完了/エラー）
var COMPLETED_AT_CELL = "E3"; // 同ジョブの完了時刻
var AUTO_REFRESH_SWITCH_CELL = "B5";       // オッズ自動更新スイッチ（ON/OFF）
var AUTO_REFRESH_LAST_UPDATED_CELL = "C5"; // 同スイッチの最終更新時刻
var SECRET_KEY = "7CGmsbp8IFy6oSbFwIHocp2fXzQ"; // 呼び出し側のURLにも同じ値を含める

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  var providedKey = (e && e.parameter) ? e.parameter.key : null;
  if (providedKey !== SECRET_KEY) {
    return jsonResponse({ status: "error", message: "invalid or missing key" });
  }

  var action = (e && e.parameter) ? e.parameter.action : null;
  if (action === "status") {
    return handleStatusRequest();
  }
  if (action === "autorefresh_status") {
    return handleAutoRefreshStatusRequest();
  }
  return handleRefreshRequest();
}

function handleRefreshRequest() {
  var result = { status: "ok" };
  try {
    var sheet = getControlPanelSheet();
    sheet.getRange(CHECKBOX_CELL).setValue(true);
  } catch (err) {
    result = { status: "error", message: String(err) };
  }
  return jsonResponse(result);
}

function handleStatusRequest() {
  try {
    var sheet = getControlPanelSheet();
    var status = sheet.getRange(STATUS_CELL).getValue();
    var completedAt = sheet.getRange(COMPLETED_AT_CELL).getValue();

    var result = { status: String(status) };
    var formatted = formatDateTime(completedAt);
    if (formatted) {
      result.completedAt = formatted;
    }
    return jsonResponse(result);
  } catch (err) {
    return jsonResponse({ status: "error", message: String(err) });
  }
}

function handleAutoRefreshStatusRequest() {
  try {
    var sheet = getControlPanelSheet();
    var on = sheet.getRange(AUTO_REFRESH_SWITCH_CELL).getValue() === true;
    var lastUpdatedAt = sheet.getRange(AUTO_REFRESH_LAST_UPDATED_CELL).getValue();

    var result = { status: "ok", autoRefreshOn: on };
    var formatted = formatDateTime(lastUpdatedAt);
    if (formatted) {
      result.lastUpdatedAt = formatted;
    }
    return jsonResponse(result);
  } catch (err) {
    return jsonResponse({ status: "error", message: String(err) });
  }
}

function getControlPanelSheet() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    throw new Error("シート「" + SHEET_NAME + "」が見つかりません");
  }
  return sheet;
}

function formatDateTime(value) {
  if (!value) return null;
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss");
  }
  return String(value);
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
