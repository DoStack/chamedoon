Vazirmatn, SIL OFL 1.1 (`OFL.txt`). Built from `@fontsource-variable/vazirmatn` 5.3.0 so both files fit the 60 KB font budget. The package files total 80.8 KB (arabic + latin), and 102.7 KB with latin-ext, which is dropped.

```
fonttools varLib.instancer vazirmatn-arabic-wght-normal.woff2 wght=400:800 -o ar.ttf
fonttools ttLib.woff2 compress ar.ttf -o vazirmatn-arabic-wght400-800.woff2
fonttools varLib.instancer vazirmatn-latin-wght-normal.woff2 wght=400:800 -o lat.ttf
pyftsubset lat.ttf --unicodes="U+20-7E,U+A0,U+C4,U+D6,U+DC,U+DF,U+E4,U+F6,U+FC,U+20AC,U+2013,U+2014,U+2019" --flavor=woff2 --layout-features='*' --output-file=vazirmatn-latin-wght400-800.woff2
```

Latin keeps ASCII and the German letters. Other Latin characters fall back to the system font.
