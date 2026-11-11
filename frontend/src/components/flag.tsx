import CA from 'country-flag-icons/react/3x2/CA'
import DE from 'country-flag-icons/react/3x2/DE'
import lionSun from '@/assets/flags/ir-lionsun.svg?no-inline'

const FLAGS: Record<string, typeof CA> = { CA, DE }
// Courier countries load as image files, so they cost the main bundle nothing.
const FILES = import.meta.glob<string>('/node_modules/country-flag-icons/3x2/{AE,AM,AT,CH,DK,FR,GB,IQ,IT,NL,NO,SE,TR,US}.svg', { eager: true, import: 'default', query: '?no-inline' })
const BOX = 'h-4 w-6 shrink-0 rounded-[2px] ring-1 ring-border'

function fileFor(code: string) {
  return code === 'IR' ? lionSun : FILES[`/node_modules/country-flag-icons/3x2/${code}.svg`]
}

/** Iran is always the lion-and-sun flag, never the current one. */
export function Flag({ code }: { code: string }) {
  const Icon = FLAGS[code]
  if (Icon) return <Icon aria-hidden className={BOX} />
  const src = fileFor(code)
  return src ? <img src={src} alt="" aria-hidden className={`${BOX} object-cover`} /> : null
}
