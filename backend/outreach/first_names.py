"""First names written in Latin letters that we greet by when the display name is that one word alone.

A single Latin word is often a handle («Amix», audit 2026-11-10 #331, rule 19): greeting it with «جان» reads wrong.
Only a name on this list is used; any other single Latin word gets «سلام وقت بخیر،». Lower case, folded.
"""

PERSIAN = {
    "abbas", "abdollah", "abol", "abolfazl", "afshin", "ahmad", "akbar", "ali", "alireza", "amin", "amir", "amirali",
    "amirhossein", "amirreza", "arash", "arman", "armin", "arsalan", "arya", "aryan", "ashkan", "babak", "bahador",
    "bahman", "bahram", "behnam", "behrad", "behrooz", "behrouz", "behzad", "benyamin", "bijan", "borna", "danial",
    "daniyal", "dariush", "darius", "davood", "davoud", "ebrahim", "ehsan", "elyas", "erfan", "esmaeil", "faramarz",
    "farbod", "farhad", "farid", "farshad", "farshid", "farzad", "farzin", "fereydoon", "hadi", "hamed", "hamid",
    "hamidreza", "hassan", "hasan", "hesam", "hessam", "homayoun", "hooman", "hootan", "hossein", "hosein", "houman",
    "iman", "javad", "kamran", "kaveh", "kambiz", "keyvan", "kian", "kiarash", "kourosh", "kurosh", "mahdi", "mahmoud",
    "majid", "mani", "mansour", "masoud", "matin", "mehdi", "mehran", "mehrdad", "meysam", "milad", "mohammad",
    "mohamad", "mohammadreza", "mohsen", "mojtaba", "morteza", "mostafa", "nader", "navid", "nima", "omid", "parham",
    "parsa", "payam", "pedram", "peyman", "pooria", "pouria", "pouya", "pooya", "ramin", "reza", "rostam", "saeed",
    "saeid", "said", "sajad", "sajjad", "salar", "saman", "sami", "sasan", "sepehr", "shahab", "shahin", "shahram",
    "shahriar", "shayan", "siamak", "siavash", "sina", "soheil", "soroush", "sorush", "taha", "vahid", "yaser",
    "yasin", "yousef", "yusef", "zia",
    "afsaneh", "akram", "anahita", "arezoo", "arezou", "atefeh", "atena", "azadeh", "azam", "bahar", "baharak",
    "bahareh", "banafsheh", "behnaz", "dorsa", "elaheh", "elham", "elnaz", "farah", "farideh", "farnaz", "farzaneh",
    "fatemeh", "fereshteh", "golnaz", "ghazal", "haleh", "hana", "hanieh", "hasti", "helia", "hoda", "laleh", "leila",
    "leyla", "mahsa", "mahnaz", "mahtab", "mandana", "marjan", "maryam", "marzieh", "masoumeh", "mina", "minoo",
    "mitra", "mona", "mozhgan", "nadia", "nahid", "narges", "nasim", "nasrin", "nazanin", "neda", "negar", "negin",
    "niloofar", "niloufar", "nilufar", "nooshin", "pantea", "parisa", "parastoo", "pardis", "parinaz", "parvaneh",
    "pegah", "raha", "ramona", "roya", "sahar", "samaneh", "samira", "sanaz", "sara", "sarah", "sepideh", "setareh",
    "shabnam", "shadi", "shahrzad", "shaghayegh", "shiva", "shirin", "simin", "soheila", "solmaz", "somayeh",
    "sogand", "soudabeh", "susan", "tahereh", "tara", "termeh", "yalda", "yasaman", "yasamin", "zahra", "zeinab",
    "ziba", "zohreh",
}  # fmt: skip

WESTERN = {
    "adam", "alex", "christopher", "christina", "catherine", "charles", "george", "henry", "jack", "joseph", "kate", "matthew", "mary", "olivia", "patrick", "richard", "william", "alexander", "alice", "amanda", "amy", "andrew", "anna", "anne", "anton", "ben", "benjamin",
    "chris", "christian", "daniel", "david", "emily", "emma", "eric", "eva", "felix", "frank", "hannah", "james",
    "jan", "jana", "jason", "jennifer", "jessica", "john", "jonas", "julia", "julian", "kevin", "laura", "lena", "leon",
    "lisa", "lucas", "luca", "lukas", "marco", "maria", "marie", "mark", "martin", "max", "michael", "nick", "nina",
    "noah", "oliver", "paul", "peter", "philipp", "robert", "ryan", "sam", "sebastian", "simon", "sophia", "sophie",
    "stefan", "thomas", "tim", "tom", "victoria",
}  # fmt: skip

MORE = set("""
sali mobina mojgan vira alidad pouyan poyan saba roozbeh rouzbeh roozbe saniya bita jamshid neusha pari
ahmadreza lida taraneh rozhin parto hosain azi nika moriya kimia kimiya ilia ilya yeganeh ava aida
ayda delaram diba donya dina fariba golnar hedieh homa katayoun kiana ladan mahshid mahya maede maedeh
mahdieh malihe mehri mehrnaz mehrnoosh nastaran nazila nazli niki nilou parmis parya raheleh rana rojin rozita
sadaf sahel sama samin sanam saghar sevil sheida shima soraya tina vida yasi zari zhila amirmohammad
arsham arshia ario aidin aydin bardia barbod daryoush esfandiar farhood farrokh ghasem hamoon heydar hirad houshang
jalal jamal kasra kamyar khashayar mahan mahyar mazyar mehrshad mehrzad mohammadali nariman nasser pejman puya ramtin
sadegh salman shahrokh shervin sohrab taher yashar yazdan zaniar koorosh dara darya abtin artin arian arin
arvin aram behrang bamdad farzam foad iraj jahangir kaivan mamad mehrab mobin mohammadhossein mohammadamin nojan payman
rasoul rasool rahim sahand samad sobhan soroosh tohid yahya mahboobeh mahboubeh mahin mehrangiz minoo nooshin parvin
pouran roshanak sima sudabeh tahmineh tannaz atousa avin baran dorna elina elmira golshan hiva kosar kowsar
mahdis melika mahla mana nazgol paria pania rose roza saeedeh sahra sara shadi sogol tarane tarlan
tiam yeganeh zhaleh naz
""".split())  # seen on production, 2026-11-10

KNOWN = PERSIAN | MORE | WESTERN
