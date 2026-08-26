"use client"

import * as React from "react"
import { CheckCircle2, ChevronDown, FolderOpen, Globe2, Monitor, Moon, Sun, Trash2 } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Field, FieldContent, FieldDescription, FieldGroup, FieldTitle } from "@/components/ui/field"
import { cn } from "@/lib/utils"
import { agentDesktop, type AgentConfig, type AgentState } from "@/lib/bridge"
import type { Messages } from "@/lib/messages"
import { toast } from "@/components/ui/sonner"
import { useTheme } from "@/components/theme-provider"

type AppearanceMode = "system" | "light" | "dark"
type LocaleMode = "system" | "en" | "zh"

const APPEARANCE_OPTIONS: Array<{ id: AppearanceMode; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: "system", label: "systemTheme", icon: Monitor },
  { id: "light", label: "lightTheme", icon: Sun },
  { id: "dark", label: "darkTheme", icon: Moon },
]

const LANGUAGE_OPTIONS: Array<{ id: LocaleMode; label: string }> = [
  { id: "system", label: "systemLanguage" },
  { id: "en", label: "english" },
  { id: "zh", label: "simplifiedChinese" },
]

function FieldRow({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Field orientation="horizontal" className="border-b border-border last:border-b-0">
      <FieldContent>
        <FieldTitle>{title}</FieldTitle>
        {description ? <FieldDescription>{description}</FieldDescription> : null}
      </FieldContent>
      {children}
    </Field>
  )
}

function SettingSwitchField({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <FieldRow title={label} description={description}>
      <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label={label} />
    </FieldRow>
  )
}

function InfoRow({ label, value, action, last = false }: { label: string; value: React.ReactNode; action?: React.ReactNode; last?: boolean }) {
  return (
    <div className={cn("flex items-center gap-4 px-1 py-3", !last && "border-b border-border")}>
      <div className="w-32 shrink-0 text-sm text-muted-foreground">{label}</div>
      <div className="min-w-0 flex-1 text-sm">{value}</div>
      {action}
    </div>
  )
}

function SettingActionField({ label, action, last = false }: { label: string; action: React.ReactNode; last?: boolean }) {
  return <InfoRow label={label} value={<span />} action={action} last={last} />
}

export function SettingsView({
  t,
  state,
  busy,
  isRunning,
}: {
  t: Messages
  state: AgentState | null
  busy: string | null
  isRunning: boolean
}) {
  const [config, setConfig] = React.useState<AgentConfig | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [savedFlash, setSavedFlash] = React.useState(false)
  const { resolvedTheme, setTheme } = useTheme()

  React.useEffect(() => {
    agentDesktop()
      .getConfig()
      .then((c) => setConfig(c))
      .catch(() => {})
  }, [])

  const appearance = (state?.appearance as AppearanceMode) || "system"
  const locale = (state?.locale as LocaleMode) || "system"

  async function saveConfig() {
    if (!config) return
    setSaving(true)
    try {
      const saved = await agentDesktop().saveConfig(config)
      setConfig(saved)
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 1500)
      if (isRunning) {
        toast.success(t.configSaved)
      } else {
        toast.success(t.configSaved)
      }
    } catch (e) {
      toast.error("Error", e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function updateSetting(patch: { appearance?: AppearanceMode; locale?: LocaleMode }) {
    try {
      await agentDesktop().saveSettings(patch)
    } catch (e) {
      /* ignore */
    }
  }

  function onAppearanceChange(mode: AppearanceMode) {
    setTheme(mode)
    void updateSetting({ appearance: mode })
  }

  async function onOpen(fn: () => Promise<string>) {
    try {
      await fn()
    } catch (e) {
      toast.error("Error", e instanceof Error ? e.message : String(e))
    }
  }

  async function clearSessions() {
    try {
      await agentDesktop().clearSessions()
      toast.success(t.clearSessionsDone)
    } catch (e) {
      toast.error("Error", e instanceof Error ? e.message : String(e))
    }
  }

  async function factoryReset() {
    try {
      await agentDesktop().factoryReset()
    } catch (e) {
      /* app 即将重启，忽略 */
    }
  }

  return (
    <div className="scroll-area h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-3xl space-y-3">
        {/* 启动设置 */}
        <Card>
          <CardHeader>
            <CardTitle>{t.startupTitle}</CardTitle>
          </CardHeader>
          <CardContent>
            <FieldGroup className="divide-y divide-border">
              <SettingSwitchField
                label={t.launchAtLogin}
                description={t.launchAtLoginHint}
                checked={Boolean(state?.openAtLogin)}
                onCheckedChange={(v) => void agentDesktop().saveSettings({ openAtLogin: v })}
              />
              <SettingSwitchField
                label={t.silentLaunch}
                description={t.silentLaunchHint}
                checked={Boolean(state?.silentLaunch)}
                onCheckedChange={(v) => void agentDesktop().saveSettings({ silentLaunch: v })}
              />
            </FieldGroup>
          </CardContent>
        </Card>

        {/* Agent 配置 */}
        <Card>
          <CardHeader>
            <CardTitle>{t.agentConfigTitle}</CardTitle>
            <CardDescription>{t.agentConfigDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <FieldRow title={t.pythonPath}>
                <div className="flex w-full items-center gap-2">
                  <Input
                    className="flex-1 font-mono text-xs"
                    value={config?.python || ""}
                    placeholder=".venv\\Scripts\\python.exe"
                    onChange={(e) => setConfig((c) => (c ? { ...c, python: e.target.value } : c))}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const p = await agentDesktop().selectPython()
                      if (p) setConfig((c) => (c ? { ...c, python: p } : c))
                    }}
                  >
                    <FolderOpen className="size-3.5" />
                    {t.open}
                  </Button>
                </div>
              </FieldRow>
              <FieldRow title={t.projectDir}>
                <div className="flex w-full items-center gap-2">
                  <Input
                    className="flex-1 font-mono text-xs"
                    value={config?.projectDir || ""}
                    placeholder="D:\\Code\\tvrcgo-agent"
                    onChange={(e) => setConfig((c) => (c ? { ...c, projectDir: e.target.value } : c))}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const p = await agentDesktop().selectProject()
                      if (p) setConfig((c) => (c ? { ...c, projectDir: p } : c))
                    }}
                  >
                    <FolderOpen className="size-3.5" />
                    {t.open}
                  </Button>
                </div>
              </FieldRow>
              <FieldRow title={t.agentPort}>
                <Input
                  className="w-24 font-mono text-xs"
                  type="number"
                  value={config?.port ?? 8765}
                  onChange={(e) => setConfig((c) => (c ? { ...c, port: Number(e.target.value) || 8765 } : c))}
                />
              </FieldRow>
              <FieldGroup className="flex flex-row justify-end gap-2">
                {savedFlash ? (
                  <div className="flex items-center gap-2 rounded-md border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-sm">
                    <CheckCircle2 className="size-4 text-emerald-500" />
                    {t.configSaved}
                  </div>
                ) : null}
                <Button onClick={() => void saveConfig()} disabled={saving || !config}>
                  <CheckCircle2 className="size-4" />
                  {t.saveConfig}
                </Button>
              </FieldGroup>
            </FieldGroup>
          </CardContent>
        </Card>

        {/* 本地文件 */}
        <Card>
          <CardHeader>
            <CardTitle>{t.localFilesTitle}</CardTitle>
          </CardHeader>
          <CardContent>
            <InfoRow
              label={t.configPath}
              value={<span className="break-all font-mono text-xs text-muted-foreground">{state?.configPath || "-"}</span>}
              action={
                <Button variant="ghost" size="xs" onClick={() => void onOpen(() => agentDesktop().openConfigFolder())}>
                  {t.openFolder}
                </Button>
              }
            />
            <InfoRow
              label={t.sessionsDir}
              value={<span className="break-all font-mono text-xs text-muted-foreground">{state?.sessionsDir || "-"}</span>}
              action={
                <Button variant="ghost" size="xs" onClick={() => void onOpen(() => agentDesktop().openSessionsFolder())}>
                  {t.openFolder}
                </Button>
              }
            />
            <InfoRow
              label={t.logPath}
              value={<span className="break-all font-mono text-xs text-muted-foreground">{state?.logPath || "-"}</span>}
              action={
                <Button variant="ghost" size="xs" onClick={() => void onOpen(() => agentDesktop().openLogFile())}>
                  {t.openFolder}
                </Button>
              }
              last
            />
          </CardContent>
        </Card>

        {/* 外观 */}
        <Card>
          <CardHeader>
            <CardTitle>{t.appearanceTitle}</CardTitle>
          </CardHeader>
          <CardContent>
            <FieldGroup className="divide-y divide-border">
              <FieldRow title={t.theme} description={t.themeDescription}>
                <DropdownMenu>
                  <DropdownMenuTrigger>
                    <Button type="button" variant="outline" className="min-w-40 justify-between">
                      {(() => {
                        const current = APPEARANCE_OPTIONS.find((o) => o.id === appearance) ?? APPEARANCE_OPTIONS[0]
                        const Icon = current.icon
                        return (
                          <>
                            <Icon className="size-4" />
                            {t[current.label as keyof Messages]}
                            <ChevronDown className="size-4" />
                          </>
                        )
                      })()}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuRadioGroup value={appearance} onValueChange={(v) => onAppearanceChange(v as AppearanceMode)}>
                      {APPEARANCE_OPTIONS.map((theme) => {
                        const Icon = theme.icon
                        return (
                          <DropdownMenuRadioItem key={theme.id} value={theme.id}>
                            <Icon className="size-4" />
                            {t[theme.label as keyof Messages]}
                          </DropdownMenuRadioItem>
                        )
                      })}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </FieldRow>
              <FieldRow title={t.language} description={t.languageDescription}>
                <DropdownMenu>
                  <DropdownMenuTrigger>
                    <Button type="button" variant="outline" className="min-w-40 justify-between">
                      <Globe2 className="size-4" />
                      {t[(LANGUAGE_OPTIONS.find((l) => l.id === locale) ?? LANGUAGE_OPTIONS[0]).label as keyof Messages]}
                      <ChevronDown className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuRadioGroup value={locale} onValueChange={(v) => void updateSetting({ locale: v as LocaleMode })}>
                      {LANGUAGE_OPTIONS.map((lang) => (
                        <DropdownMenuRadioItem key={lang.id} value={lang.id}>
                          {t[lang.label as keyof Messages]}
                        </DropdownMenuRadioItem>
                      ))}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </FieldRow>
            </FieldGroup>
          </CardContent>
        </Card>

        {/* 危险操作 */}
        <div className="pt-2">
          <h3 className="mb-2 px-1 text-sm font-medium text-destructive">{t.dangerTitle}</h3>
        </div>
        <Card className="border-destructive/25">
          <CardHeader>
            <CardTitle>{t.clearSessions}</CardTitle>
            <CardDescription>{t.clearSessionsDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex justify-end">
              <AlertDialog>
                <AlertDialogTrigger>
                  <Button variant="destructive">
                    <Trash2 className="size-4" />
                    {t.clearSessions}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t.clearSessionsConfirmTitle}</AlertDialogTitle>
                    <AlertDialogDescription>{t.clearSessionsConfirmDescription}</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t.cancel}</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={() => void clearSessions()}>
                      {t.clearSessions}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </CardContent>
        </Card>
        <Card className="border-destructive/25">
          <CardHeader>
            <CardTitle>{t.factoryResetTitle}</CardTitle>
            <CardDescription>{t.factoryResetDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex justify-end">
              <AlertDialog>
                <AlertDialogTrigger>
                  <Button variant="destructive" disabled={Boolean(busy)}>
                    <Trash2 className="size-4" />
                    {t.factoryResetAction}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t.factoryResetConfirmTitle}</AlertDialogTitle>
                    <AlertDialogDescription>{t.factoryResetConfirmDescription}</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t.cancel}</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={() => void factoryReset()}>
                      {t.factoryResetAction}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
