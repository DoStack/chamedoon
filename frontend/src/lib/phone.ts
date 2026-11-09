// Phone numbers in E.164 (+ and 8 to 15 digits). Same rules as backend/home/phones.py, so the number the poster sees
// before publishing is the one stored.

const DIGITS: Record<string, string> = Object.fromEntries(
  [...'۰۱۲۳۴۵۶۷۸۹', ...'٠١٢٣٤٥٦٧٨٩'].map((char, index) => [char, String(index % 10)]),
)
const ALLOWED = /^\+?[\d\s\-().]+$/

// 00 is the international prefix (00989356706868 → +989356706868). Without + or 00 a number reads in the listing's
// country: ten digits in Canada or the US get +1, a leading 0 in Germany becomes +49. 09… with 11 digits is Iranian.
export function toE164(raw: string, country = ''): string {
  const text = raw.replace(/[۰-۹٠-٩]/g, (char) => DIGITS[char] ?? char).trim()
  if (!text || !ALLOWED.test(text)) return ''
  let digits = text.replace(/\D/g, '')
  if (text.startsWith('+')) {
    // Already international.
  } else if (digits.startsWith('00')) digits = digits.slice(2)
  else if ((country === 'CA' || country === 'US') && digits.length === 10 && !/^[01]/.test(digits)) digits = `1${digits}`
  else if (digits.length === 11 && digits.startsWith('09')) digits = `98${digits.slice(1)}`
  else if (country === 'DE' && digits.startsWith('0')) digits = `49${digits.slice(1)}`
  if (digits.length < 8 || digits.length > 15 || digits.startsWith('0')) return ''
  return `+${digits}`
}

// wa.me takes the number without + or spaces.
export const whatsappUrl = (phone: string) => `https://wa.me/${phone.replace(/\D/g, '')}`
