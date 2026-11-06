import { CircleAlert, Megaphone, Users } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { JoinChat } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { openExternal } from '@/lib/links'
import { track } from '@/lib/report'

export type JoinMoment = 'post' | 'contact'

type Props = {
  open: boolean
  moment: JoinMoment
  chats: JoinChat[]
  // The city in Persian («تورنتو»), or the country for a country channel.
  place: string
  checking: boolean
  stillOut: boolean
  onConfirm: (via: 'tap' | 'focus') => void
  onClose: () => void
}

function chatsWord(chats: JoinChat[]) {
  const kinds = new Set(chats.map((chat) => chat.kind))
  if (kinds.size > 1) return 'کانال و گروه'
  return kinds.has('group') ? 'گروه' : 'کانال'
}

export function joinTitle(chats: JoinChat[], place: string) {
  return `لطفاً اول عضو ${chatsWord(chats)} چمدون ${place} شوید`
}

function Avatar({ chat }: { chat: JoinChat }) {
  const [broken, setBroken] = useState(false)
  const Icon = chat.kind === 'group' ? Users : Megaphone
  if (broken || !chat.photo_url) {
    return (
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-muted" aria-hidden>
        <Icon className="size-5" />
      </span>
    )
  }
  return <img src={chat.photo_url} alt="" className="size-10 shrink-0 rounded-full bg-muted object-cover" onError={() => setBroken(true)} />
}

// The join gate: the city's channel and group, one tap each, then «عضو شدم». Rechecks by itself when Telegram hands
// focus back after a join. The link sits behind the button and is never shown.
export function JoinSheet({ open, moment, chats, place, checking, stillOut, onConfirm, onClose }: Props) {
  const joining = useRef(false)
  useEffect(() => {
    if (!open) return
    const back = () => {
      if (document.visibilityState === 'visible' && joining.current) {
        joining.current = false
        onConfirm('focus')
      }
    }
    document.addEventListener('visibilitychange', back)
    return () => document.removeEventListener('visibilitychange', back)
  }, [open, onConfirm])

  const join = (chat: JoinChat) => {
    track('join_tap', { moment, kind: chat.kind })
    joining.current = true
    openExternal(chat.join_url)
  }

  return (
    <AppSheet
      open={open}
      onClose={onClose}
      label={joinTitle(chats, place)}
      title={joinTitle(chats, place)}
      testId={moment === 'contact' ? 'gate-sheet' : 'post-join-sheet'}
      foot={
        <Button variant="outline" className="w-full" disabled={checking} onClick={() => onConfirm('tap')}>
          {checking ? <Spinner /> : null}
          {moment === 'contact' ? 'عضو شدم، نمایش تماس' : 'عضو شدم، ثبت آگهی'}
        </Button>
      }
    >
      <p className="m-0 text-sm text-muted-foreground">
        {moment === 'contact' ? `آگهی‌های تازه‌ی ${place} هر روز در این کانال منتشر می‌شود.` : 'آگهی شما در همین کانال منتشر می‌شود.'}
      </p>
      {stillOut ? (
        <Alert variant="destructive" data-testid="still-out">
          <CircleAlert />
          <AlertDescription>هنوز عضو نشده‌اید.</AlertDescription>
        </Alert>
      ) : null}
      {chats.map((chat) => (
        <Card key={chat.kind} className="flex-row items-center gap-3 p-3" data-testid={`join-${chat.kind}`}>
          <Avatar chat={chat} />
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            {chat.kind === 'group' ? <Users className="size-4 shrink-0 text-muted-foreground" aria-label="گروه" /> : <Megaphone className="size-4 shrink-0 text-muted-foreground" aria-label="کانال" />}
            <span className="line-clamp-2 text-sm leading-5 font-semibold">{chat.title}</span>
          </div>
          <Button size="sm" onClick={() => join(chat)}>
            عضویت
          </Button>
        </Card>
      ))}
    </AppSheet>
  )
}
