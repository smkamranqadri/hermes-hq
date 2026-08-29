import { useState } from 'react'
import { post, setCsrf } from '../api'
import { Btn, TextInput } from './Modal'

export function LoginScreen({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState(''); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { const r = await post<{ csrf: string }>('/api/login', { password: pw }); setCsrf(r.csrf); onDone() }
    catch (x) { setErr(x instanceof Error ? x.message : 'login failed') } finally { setBusy(false) }
  }
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <form onSubmit={submit} className="glass-strong w-full max-w-sm rounded-2xl p-6 shadow-2xl">
        <div className="mb-5 flex items-center gap-2.5"><img src="/icon.svg" alt="" className="size-7" /><span className="hq-wordmark text-sm font-bold uppercase">Hermes // HQ</span></div>
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Password</p>
        <TextInput type="password" autoFocus value={pw} onChange={e => setPw(e.target.value)} placeholder="from HERMES_HQ_PASSWORD or the serve log" />
        {err && <p className="mt-2 text-xs text-error">{err}</p>}
        <Btn type="submit" disabled={busy || !pw} className="mt-4 w-full">{busy ? '…' : 'Sign in'}</Btn>
      </form>
    </div>
  )
}
