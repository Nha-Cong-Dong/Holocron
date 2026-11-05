"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Button, Field, Switch } from "@/components/ui";
import {
  consentControl,
  emptyForm,
  formTitle,
  keyLabel,
  keyStorageWarning,
  PROVIDER_TEMPLATES,
  type ProviderForm,
  roleView,
  templateInfo,
  testMessage,
  toCreateBody,
  validateForm,
} from "@/lib/ai-settings";
import { api, hasAccessToken, setAccessToken, toUiError } from "@/lib/api";
import type { AiConsentState, AiStatus, ProviderConfig, ProviderList, ProviderTemplate } from "@/types/holocron";

import styles from "./AiConnection.module.css";
import settings from "./SettingsView.module.css";

interface Loaded {
  status: AiStatus;
  consent: AiConsentState;
  providers: ProviderList;
}

interface TestOutcome {
  ok: boolean;
  text: string;
}

/**
 * Cài đặt › "Kết nối AI" (SPEC §5.16, §6.11): trạng thái từng vai, công tắc đồng ý gửi nội dung tài liệu, ô "Mã truy
 * cập" (giữ trong RAM), danh sách nhà cung cấp và form thêm. Khoá API chỉ nằm trong state của form đến lúc gửi rồi bị
 * xoá; giao diện chỉ hiển thị 4 ký tự cuối do backend trả. Mọi con số (chi phí, trần) do backend tính.
 */
export function AiConnection() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [form, setForm] = useState<ProviderForm | null>(null);
  const [formErrors, setFormErrors] = useState<ReturnType<typeof validateForm>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestOutcome>>({});
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const ids = useId();
  const consentLabelId = `${ids}-consent`;
  const consentHintId = `${ids}-consent-hint`;

  const reload = useCallback(async (): Promise<void> => {
    try {
      const [status, consent, providers] = await Promise.all([
        api.getAiStatus(),
        api.getAiConsent(),
        api.listProviders(),
      ]);
      setLoaded({ status, consent, providers });
      setLoadError(null);
    } catch (error) {
      setLoadError(toUiError(error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Chạy một thao tác ghi: báo lỗi đã phân loại, tải lại trạng thái khi xong. */
  async function run(key: string, action: () => Promise<void>): Promise<void> {
    setBusy(key);
    setActionError(null);
    try {
      await action();
      await reload();
    } catch (error) {
      setActionError(toUiError(error).message);
    } finally {
      setBusy(null);
    }
  }

  function applyToken(): void {
    setAccessToken(token);
    setActionError(null);
    void reload();
  }

  function startForm(preset: ProviderTemplate): void {
    setForm(emptyForm(preset));
    setFormErrors({});
  }

  function submitForm(): void {
    if (form === null) return;
    const errors = validateForm(form);
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    const body = toCreateBody(form);
    // Xoá khoá khỏi state ngay khi gửi, dù thành công hay lỗi: không giữ khoá lâu hơn một lần gửi.
    setForm({ ...form, apiKey: "" });
    void run("create", async () => {
      await api.createProvider(body);
      setForm(null);
    });
  }

  function testProvider(provider: ProviderConfig): void {
    const role = provider.models.vlm !== null ? "vlm" : "llm";
    void run(`test:${provider.id}`, async () => {
      const result = await api.testProvider(provider.id, { action: "chat", role });
      setTests((current) => ({ ...current, [provider.id]: { ok: result.ok, text: testMessage(result) } }));
    });
  }

  function removeProvider(provider: ProviderConfig): void {
    if (confirmDelete !== provider.id) {
      setConfirmDelete(provider.id);
      return;
    }
    setConfirmDelete(null);
    void run(`delete:${provider.id}`, async () => {
      await api.deleteProvider(provider.id);
      setTests((current) => {
        const { [provider.id]: _removed, ...rest } = current;
        return rest;
      });
    });
  }

  async function changeConsent(next: boolean): Promise<void> {
    await api.setAiConsent(next);
  }

  const control =
    loaded === null ? null : consentControl(loaded.consent, loaded.status.write_protection, hasAccessToken());
  const writable =
    loaded !== null && loaded.providers.write_protection === "token" && loaded.providers.settings_persistent;

  return (
    <section className={settings.panel} aria-labelledby="settings-ai" aria-busy={loaded === null && loadError === null}>
      <div>
        <h2 id="settings-ai" className={settings.heading}>
          Kết nối AI
        </h2>
        <p className={settings.caption}>
          Cho Holocron nhờ AI đọc ảnh và soạn câu hỏi. Khoá API chỉ nằm trên máy này và không hiện lại sau khi lưu.
        </p>
      </div>

      {loadError !== null && (
        <div className={styles.error} role="alert">
          <p>{loadError}</p>
          <div className={styles.actions}>
            <Button variant="secondary" onClick={() => void reload()}>
              Thử lại
            </Button>
          </div>
        </div>
      )}
      {actionError !== null && (
        <p className={styles.error} role="alert">
          {actionError}
        </p>
      )}

      {loaded !== null && control !== null && (
        <>
          <ul className={styles.roles} aria-label="Trạng thái từng vai">
            {loaded.status.roles.map((role) => {
              const view = roleView(role);
              return (
                <li key={role.role} className={styles.role}>
                  <span className={styles.roleName}>{view.name}</span>
                  <span className={styles.tone} data-tone={view.tone}>
                    {view.text}
                  </span>
                </li>
              );
            })}
          </ul>

          <div className={settings.row}>
            <div>
              <p id={consentLabelId} className={settings.rowTitle}>
                Cho phép gửi nội dung tài liệu (ảnh và đoạn chữ) tới nhà cung cấp AI khi nhập
              </p>
              <p id={consentHintId} className={settings.caption}>
                {control.reason ?? "Mặc định tắt. Bật thì ảnh trang và đoạn chữ được gửi tới nhà cung cấp đã chọn."}
              </p>
            </div>
            <Switch
              checked={loaded.consent.enabled}
              disabled={control.disabled || busy === "consent"}
              onChange={(next) => void run("consent", () => changeConsent(next))}
              labelledBy={consentLabelId}
              describedBy={consentHintId}
            />
          </div>

          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              applyToken();
            }}
          >
            <Field
              label="Mã truy cập"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              hint="Giá trị của HOLOCRON_REQUIRE_TOKEN. Chỉ giữ trong trình duyệt tới khi tải lại trang."
            />
            <div className={styles.actions}>
              <Button variant="secondary" type="submit">
                Dùng mã này
              </Button>
            </div>
          </form>

          <h3 className={styles.subheading}>Nhà cung cấp</h3>
          {loaded.providers.providers.length === 0 ? (
            <p className={settings.caption}>Chưa có nhà cung cấp nào. Thêm một nhà cung cấp từ mẫu bên dưới.</p>
          ) : (
            <ul className={styles.providers}>
              {loaded.providers.providers.map((provider) => {
                const warning = keyStorageWarning(provider);
                const test = tests[provider.id];
                const readOnly = provider.source === "env";
                return (
                  <li key={provider.id} className={styles.provider}>
                    <span className={styles.roleName}>{provider.label}</span>
                    <p className={styles.meta}>
                      {provider.base_url ?? "Cấu hình bằng biến môi trường (chỉ xem)"} · {keyLabel(provider)}
                    </p>
                    <p className={styles.meta}>
                      Đã dùng {provider.spent_usd} / {provider.budget_usd} USD
                      {provider.budget_exhausted ? " (chạm trần)" : ""}
                      {provider.enabled ? "" : " · đang tắt"}
                    </p>
                    {warning !== null && <p className={styles.warning}>{warning}</p>}
                    {test !== undefined && (
                      <p className={styles.result} data-ok={test.ok} role="status">
                        {test.text}
                      </p>
                    )}
                    {!readOnly && (
                      <div className={styles.actions}>
                        <Button
                          variant="secondary"
                          busy={busy === `test:${provider.id}`}
                          busyLabel="Đang kiểm thử"
                          disabled={!writable}
                          onClick={() => testProvider(provider)}
                        >
                          Kiểm thử
                        </Button>
                        <Button
                          variant="secondary"
                          busy={busy === `delete:${provider.id}`}
                          disabled={!writable}
                          onClick={() => removeProvider(provider)}
                        >
                          {confirmDelete === provider.id ? "Bấm lần nữa để xoá cả khoá" : "Xoá"}
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {!writable && (
            <p className={settings.caption}>
              {loaded.providers.write_protection === "token_not_configured"
                ? "Chưa thêm hay xoá được nhà cung cấp: đặt biến môi trường HOLOCRON_REQUIRE_TOKEN cho backend rồi khởi động lại."
                : "Chưa lưu được nhà cung cấp: backend chưa có thư mục dữ liệu (HOLOCRON_DATA_DIR)."}
            </p>
          )}

          {form === null ? (
            <div className={styles.actions}>
              {PROVIDER_TEMPLATES.map((info) => (
                <Button
                  key={info.template}
                  variant="secondary"
                  disabled={!writable}
                  onClick={() => startForm(info.template)}
                >
                  Thêm {info.name}
                </Button>
              ))}
            </div>
          ) : (
            <form
              className={styles.form}
              aria-label={formTitle(form)}
              onSubmit={(event) => {
                event.preventDefault();
                submitForm();
              }}
            >
              <p className={settings.caption}>{templateInfo(form.template).hint}</p>
              <Field
                label="Tên"
                value={form.label}
                error={formErrors.label}
                onChange={(event) => setForm({ ...form, label: event.target.value })}
              />
              <Field
                label="Địa chỉ dịch vụ"
                value={form.baseUrl}
                error={formErrors.baseUrl}
                autoComplete="off"
                onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
              />
              <Field
                label="Khoá API"
                type="password"
                autoComplete="off"
                value={form.apiKey}
                error={formErrors.apiKey}
                hint="Chỉ gửi một lần tới backend; sau đó chỉ hiện 4 ký tự cuối."
                onChange={(event) => setForm({ ...form, apiKey: event.target.value })}
              />
              <div className={styles.models}>
                <Field
                  label="Model đọc ảnh (VLM)"
                  value={form.modelVlm}
                  autoComplete="off"
                  onChange={(event) => setForm({ ...form, modelVlm: event.target.value })}
                />
                <Field
                  label="Model ngôn ngữ (LLM)"
                  value={form.modelLlm}
                  autoComplete="off"
                  onChange={(event) => setForm({ ...form, modelLlm: event.target.value })}
                />
              </div>
              {formErrors.models !== undefined && (
                <p className={styles.error} role="alert">
                  {formErrors.models}
                </p>
              )}
              <div className={styles.actions}>
                <Button variant="primary" type="submit" busy={busy === "create"} busyLabel="Đang lưu">
                  Lưu nhà cung cấp
                </Button>
                <Button variant="secondary" onClick={() => setForm(null)}>
                  Huỷ
                </Button>
              </div>
            </form>
          )}
        </>
      )}
    </section>
  );
}
