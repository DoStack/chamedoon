import CA from 'country-flag-icons/react/3x2/CA'
import DE from 'country-flag-icons/react/3x2/DE'

const FLAGS: Record<string, typeof CA> = { CA, DE }

export function Flag({ code }: { code: string }) {
  const Icon = FLAGS[code]
  return Icon ? <Icon aria-hidden className="h-4 w-6 shrink-0 rounded-[2px] ring-1 ring-border" /> : null
}
