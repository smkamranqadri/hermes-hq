/** The private vault (Phase 4 slice B).
 *
 * Its own Library folder, with no area/tag/project filing anywhere — a row
 * you cannot read is a row you cannot file (owner decision 2026-09-10).
 *
 * Locked is the NORMAL state, not an error: the vault re-seals on logout, on
 * an idle timeout and on every restart, including the 05:00 PKT auto-update.
 * A locked row renders as a lock icon and a date. Unlocking decrypts titles
 * for this session, so an unlocked vault reads like an ordinary list rather
 * than a wall of dates.
 *
 * The passphrase lives in React state for exactly as long as the submit takes
 * and is never persisted client-side — no localStorage, no sessionStorage.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ago, useVaultAudit, useVaultNote, useVaultNotes, useVaultStatus, vaultAttachmentUrl, vaultCreate, vaultInit, vaultLock, vaultSetPassphrase, vaultUnlock, when, type VaultNoteRow } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { BrainSubNav, useBrainCounts } from '../components/brain'
import { Markdown } from '../components/chat/Markdown'
import { Btn, Field, Modal, TextArea, TextInput } from '../components/Modal'
import { Chip, Empty, Label, Loading } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

const LOCK = '🔒'

/** Shown ONCE, at creation, and stored nowhere. It must survive the parent
 *  unmounting (creating the vault unlocks it, which swaps UnlockCard out in
 *  the same tick — that is exactly how the previous disclosure managed to
 *  flash for a single frame and never be read), so it is rendered by the PAGE
 *  and dismissed only by an explicit confirmation. */
function RecoveryCodeModal({ code, note, onDone }: { code: string; note: string; onDone: () => void }) {
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)
  return (
    <Modal title="Save your recovery code" onClose={() => { /* no accidental dismissal */ }}
      dismissible={false} elevated>
      <div className="flex flex-col gap-3" data-recovery-box>
        <p className="text-sm">This is the only time this code is shown. It is stored nowhere —
          not in the database, not in a backup, not in any log.</p>
        <div className="rounded-lg border border-accent/50 bg-inset p-3">
          <p className="select-all break-all font-mono text-sm tracking-wider" data-recovery-code>{code}</p>
        </div>
        <p className="text-xs text-muted">{note}</p>
        <p className="text-xs text-needsyou">If you lose both this code and the passphrase, the vault is gone for good.</p>
        <div className="flex flex-wrap items-center gap-2">
          <Btn kind="ghost" type="button" onClick={() => {
            // clipboard can be unavailable (insecure context) — never throw at the owner
            void navigator.clipboard?.writeText(code).then(() => setCopied(true)).catch(() => setCopied(false))
          }}>{copied ? 'Copied' : 'Copy'}</Btn>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={saved} onChange={e => setSaved(e.target.checked)} data-recovery-saved />
            I have saved it
          </label>
          <Btn className="ml-auto" disabled={!saved} onClick={onDone} data-recovery-done>Continue</Btn>
        </div>
      </div>
    </Modal>
  )
}

/** Unlock (or first-time setup). One field, submitted and immediately
 *  forgotten — the input is cleared in `finally`, so an interrupted submit
 *  does not leave the passphrase sitting in a controlled input. */
function UnlockCard({ initialized, onCreated }: { initialized: boolean; onCreated: (code: string, note: string) => void }) {
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient(); const toast = useToast()
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pass) return
    setBusy(true)
    try {
      if (initialized) { await vaultUnlock(pass); toast('Vault unlocked') }
      // No toast on creation: the recovery-code modal IS the confirmation, and
      // a toast floats over it — covering a code that is shown exactly once.
      else { const r = await vaultInit(pass); onCreated(r.recovery_code, r.recovery_note) }
      await qc.invalidateQueries({ queryKey: ['vault-status'] })
      await qc.invalidateQueries({ queryKey: ['vault-notes'] })
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'err')
    } finally {
      setPass('')                    // never linger, even on failure
      setBusy(false)
    }
  }
  return (
    <GlassCard accent="var(--hq-accent)">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div>
          <h2 className="text-sm font-semibold">{initialized ? 'Vault locked' : 'Set up the vault'}</h2>
          <p className="mt-1 text-xs text-muted">
            {initialized
              ? 'A separate passphrase — not your login password. It is never stored on this device, so the vault re-seals on every sign-out and every restart.'
              : 'Choose a passphrase (12+ characters). It is not your login password, and it is the only thing protecting these notes. You will get a one-time recovery code straight after.'}
          </p>
        </div>
        <Field label="Vault passphrase">
          {/* new-password + no autoCapitalize: browsers must not offer to save
              this, and iOS must not capitalise the first character. */}
          <TextInput type="password" value={pass} onChange={e => setPass(e.target.value)}
            autoComplete="new-password" autoCapitalize="off" autoCorrect="off"
            spellCheck={false} name="vault-passphrase" aria-label="Vault passphrase"
            placeholder={initialized ? 'Passphrase' : 'At least 12 characters'} />
        </Field>
        <div className="flex items-center gap-2">
          <Btn type="submit" busy={busy} disabled={!pass}>{initialized ? 'Unlock' : 'Create vault'}</Btn>
          <span className="text-[11px] text-muted">Nothing is saved on this device.</span>
        </div>
      </form>
    </GlassCard>
  )
}

function NewVaultNote({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState(''); const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient(); const toast = useToast()
  const save = async () => {
    if (!title.trim()) return
    setBusy(true)
    try {
      await vaultCreate(title.trim(), body)
      toast('Sealed into the vault'); qc.invalidateQueries({ queryKey: ['vault-notes'] })
      qc.invalidateQueries({ queryKey: ['vault-status'] }); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="New vault note" onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Title"><TextInput value={title} onChange={e => setTitle(e.target.value)} data-vault-title placeholder="What is it" /></Field>
        <Field label="Body" hint="Sealed with everything else — the librarian never sees any of it.">
          <TextArea value={body} onChange={e => setBody(e.target.value)} rows={6} />
        </Field>
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn onClick={() => void save()} busy={busy} disabled={!title.trim()}>Seal it</Btn>
        </div>
      </div>
    </Modal>
  )
}

/** A locked row: lock icon + date, nothing else. The server did not send a
 *  title, so there is none to leak into the DOM. */
function LockedRow({ n }: { n: VaultNoteRow }) {
  return (
    <div className="glass flex items-center gap-3 rounded-xl px-4 py-3" data-vault-row data-locked="1">
      <span aria-hidden className="text-base">{LOCK}</span>
      <span className="min-w-0 flex-1 truncate text-sm text-muted">Locked</span>
      <span className="font-mono text-[10px] text-muted">{when(n.updated_at ?? n.created_at)}</span>
    </div>
  )
}

function UnlockedRow({ n, onOpen, open }: { n: VaultNoteRow; onOpen: () => void; open: boolean }) {
  return (
    <button onClick={onOpen} data-vault-row data-locked="0"
      className={`glass block w-full rounded-xl px-4 py-3 text-left hover:bg-raised ${open ? 'border border-accent/50' : ''}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{n.title}</span>
        {n.entry_count > 0 && <span className="font-mono text-[10px] text-muted">{n.entry_count} thought{n.entry_count === 1 ? '' : 's'}</span>}
        {n.attachment_count > 0 && <Chip>{n.attachment_count} file{n.attachment_count === 1 ? '' : 's'}</Chip>}
        <span className="font-mono text-[10px] text-muted">{ago(n.updated_at ?? n.created_at)}</span>
      </div>
    </button>
  )
}

/** Change the vault passphrase.
 *
 * Added 2026-09-11. The route had existed since the vault shipped and NOTHING
 * called it, so the only way to rotate was to put the new passphrase on a
 * command line — which is exactly what you are rotating away from after one
 * has leaked. Re-wrapping keeps the same data key: nothing sealed is
 * re-encrypted and the recovery code stays valid. */
function ChangePassphrase({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const short = next.length > 0 && next.length < 12
  const mismatch = again.length > 0 && next !== again
  const save = async () => {
    setBusy(true)
    try {
      await vaultSetPassphrase(cur, next)
      toast('Passphrase changed — your recovery code still works')
      onClose()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    } finally {
      // never leave a passphrase sitting in a controlled input, even on failure
      setBusy(false); setCur(''); setNext(''); setAgain('')
    }
  }
  return (
    <Modal title="Change the vault passphrase" onClose={onClose}>
      <div className="flex flex-col gap-3" data-change-passphrase>
        <Field label="Current passphrase">
          <TextInput type="password" autoComplete="current-password" autoFocus
                     value={cur} onChange={e => setCur(e.target.value)} />
        </Field>
        <Field label="New passphrase" hint="At least 12 characters. hq.db can be read by anything running on this box, so this is the only thing protecting the contents against an offline attack.">
          <TextInput type="password" autoComplete="new-password"
                     value={next} onChange={e => setNext(e.target.value)} />
        </Field>
        <Field label="Confirm new passphrase">
          <TextInput type="password" autoComplete="new-password"
                     value={again} onChange={e => setAgain(e.target.value)} />
        </Field>
        {short && <p className="text-[11px] text-error">At least 12 characters.</p>}
        {mismatch && <p className="text-[11px] text-error">The two new passphrases do not match.</p>}
        <p className="text-[11px] text-muted">
          The same data key is re-wrapped, so nothing sealed is re-encrypted and your
          one-time recovery code keeps working.
        </p>
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
          <Btn busy={busy} disabled={!cur || next.length < 12 || next !== again}
               onClick={() => void save()}>Change it</Btn>
        </div>
      </div>
    </Modal>
  )
}

function VaultNotePanel({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useVaultNote(id)
  const n = q.data
  if (q.isLoading) return <GlassCard><Loading rows={3} /></GlassCard>
  if (q.isError || !n) return <Empty error title="Could not open that note" note="The vault may have re-sealed — unlock and try again." />
  return (
    <GlassCard className="min-w-0">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 break-words text-base font-semibold">{n.title}</h2>
        <Btn kind="ghost" onClick={onClose}>Close</Btn>
      </div>
      <p className="mb-3 font-mono text-[10px] uppercase tracking-widest text-muted">sealed · {when(n.created_at)}</p>
      <div className="prose-hq min-w-0 break-words text-sm"><Markdown text={n.body || '_(empty)_'} /></div>
      {n.attachments.length > 0 && (
        <div className="mt-4 flex flex-col gap-2">
          <Label>Attachments</Label>
          {n.attachments.map(a => (
            <a key={a.id} href={vaultAttachmentUrl(a.id)} target="_blank" rel="noreferrer"
              className="truncate text-xs text-accent-2 hover:underline">{a.name ?? `${a.kind} #${a.id}`}</a>
          ))}
        </div>
      )}
      {n.entries.length > 0 && (
        <div className="mt-4 flex flex-col gap-3">
          <Label>Thoughts</Label>
          {n.entries.map(e => (
            <div key={e.id} className="rounded-lg border border-line p-2">
              <p className="font-mono text-[10px] text-muted">{when(e.created_at)}</p>
              <p className="mt-1 whitespace-pre-wrap break-words text-xs">{e.body}</p>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

/** What the vault DESTROYED. Readable while locked on purpose: it holds no
 *  secret (a title is a sha256) and it is the owner's proof that a
 *  move-to-vault really erased the plaintext. */
function AuditCard() {
  const q = useVaultAudit()
  const rows = (q.data?.audit ?? []).filter(a => a.action === 'moved_in' || a.action === 'drained')
  if (!rows.length) return null
  return (
    <details className="mt-4">
      <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-muted">
        Destruction log ({rows.length})
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        {rows.map(a => (
          <div key={a.id} className="glass rounded-lg px-3 py-2 text-xs text-muted">
            <p className="font-mono text-[10px] uppercase tracking-wider">
              {a.action === 'moved_in' ? `note #${a.old_note_id} destroyed` : 'quarantine drained'} · {when(a.created_at)}
            </p>
            {a.action === 'moved_in' && (
              <p className="mt-1">{a.revisions} revision{a.revisions === 1 ? '' : 's'}, {a.entries} thought{a.entries === 1 ? '' : 's'}, {a.attachments} attachment{a.attachments === 1 ? '' : 's'} · title sha256 <span className="break-all font-mono text-[10px]">{a.title_sha256?.slice(0, 16)}…</span></p>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

export function BrainVault() {
  usePageTitle('Vault')
  const { inbox, review } = useBrainCounts()
  const st = useVaultStatus()
  const notes = useVaultNotes(st.data?.initialized === true)
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  // Held by the PAGE, not by UnlockCard: creating the vault unlocks it, which
  // unmounts UnlockCard in the same tick. The previous disclosure lived in
  // that component's state and so rendered for exactly one frame.
  const [newCode, setNewCode] = useState<{ code: string; note: string } | null>(null)
  const qc = useQueryClient(); const toast = useToast()
  const [changing, setChanging] = useState(false)
  const unlocked = st.data?.unlocked === true
  const rows = notes.data?.notes ?? []
  const lock = async () => {
    await vaultLock(); setOpen(null)
    qc.invalidateQueries({ queryKey: ['vault-status'] })
    qc.invalidateQueries({ queryKey: ['vault-notes'] })
    toast('Vault sealed')
  }
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      {creating && <NewVaultNote onClose={() => setCreating(false)} />}
      {changing && <ChangePassphrase onClose={() => setChanging(false)} />}
      {newCode && <RecoveryCodeModal code={newCode.code} note={newCode.note}
        onDone={() => { setNewCode(null); qc.invalidateQueries({ queryKey: ['vault-status'] }) }} />}
      <PageHeader crumb="second-brain // vault" title="Vault" right={
        <div className="flex flex-wrap items-center gap-2">
          <BrainSubNav inbox={inbox} review={review} />
          {unlocked && <Btn kind="ghost" onClick={() => setChanging(true)} data-vault-passphrase>Change passphrase</Btn>}
          {unlocked && <Btn kind="ghost" onClick={() => void lock()} data-vault-lock>Lock</Btn>}
          {unlocked && <Btn onClick={() => setCreating(true)} data-vault-new>+ Note</Btn>}
        </div>} />

      <p className="mb-4 max-w-2xl text-xs text-muted">
        Sealed at rest with a passphrase of its own. The librarian cannot reach these notes —
        they are not in the Library at all — and they carry no area, tag or project.
      </p>

      {st.isLoading && <Loading rows={3} />}
      {st.data && !unlocked && <UnlockCard initialized={st.data.initialized}
        onCreated={(code, note) => setNewCode({ code, note })} />}

      {/* A vault created before the 2026-09-10 reversal has no recovery wrap
          at all, and nothing used to say so — `has_recovery_code` was typed
          and read by no component. Forgetting the passphrase on such a vault
          is permanent, so it is stated wherever the vault is, not once. */}
      {st.data?.initialized && st.data.has_recovery_code === false && (
        <div className="mt-4 rounded-xl border border-needsyou/40 bg-needsyou/10 p-3 text-xs text-needsyou"
             data-no-recovery>
          <p className="font-semibold">This vault has no recovery code.</p>
          <p className="mt-1">Forgetting the passphrase would be permanent. Run
            <span className="mx-1 font-mono">wm vault recovery-code --regenerate</span>
            on the server to mint one.</p>
        </div>
      )}

      {st.data?.initialized && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <div className="mb-2 flex items-center justify-between">
              <Label>{unlocked ? 'Vault' : 'Locked'}</Label>
              <span className="font-mono text-[10px] text-muted">{rows.length} note{rows.length === 1 ? '' : 's'}</span>
            </div>
            {notes.isLoading && <Loading rows={3} />}
            {notes.data && rows.length === 0 && (
              <Empty title="The vault is empty"
                note={unlocked ? 'Seal something with + Note, or move a Library note in from its page.' : 'Unlock to add something.'} />
            )}
            <div className="flex flex-col gap-2">
              {rows.map(n => n.locked
                ? <LockedRow key={n.id} n={n} />
                : <UnlockedRow key={n.id} n={n} open={open === n.id} onOpen={() => setOpen(open === n.id ? null : n.id)} />)}
            </div>
            <AuditCard />
          </div>
          <div className="min-w-0">
            {open != null && unlocked && <VaultNotePanel id={open} onClose={() => setOpen(null)} />}
          </div>
        </div>
      )}
    </section>
  )
}
