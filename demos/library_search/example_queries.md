# library_search: example queries on a SAMPLE inventory

> SAMPLE / DEMO DATA. The inventory is a made-up stock list of well-known classic titles (inventory_sample.csv); shelves, copy counts and ISBNs (non-issued 999 prefix) are invented for the demo and do not describe any real library. Captured with: python -m library_search search inventory_sample.csv <queries> --limit 5

Inventory `inventory_sample.csv`: 67 rows read, 4 duplicates merged, 63 titles, 58 in stock, 26 synonym groups.

## 1. `تنمية ذاتية`

Also searched: تنمية ذاتية → تطوير الذات, تنمية بشرية, self-help, self improvement, personal development

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | سر النجاح | صموئيل سمايلز، ترجمة يعقوب صروف | AR-SLF-01 | 1 | تنمية ذاتية (category); Self Help (description, via 'تنمية ذاتية') |
| 2 | Self-Help | Samuel Smiles | EN-SLF-01 | 2 | Self Help (title, category, via 'تنمية ذاتية') |
| 3 | As a Man Thinketh | James Allen | EN-SLF-02 | 1 | Self help (category, via 'تنمية ذاتية') |

_1 matching title(s) hidden because no copies are available._

## 2. `self help`

Also searched: self-help → تنمية ذاتية, تطوير الذات, تنمية بشرية, self improvement, personal development

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | Self-Help | Samuel Smiles | EN-SLF-01 | 2 | Self Help (title, category) |
| 2 | سر النجاح | صموئيل سمايلز، ترجمة يعقوب صروف | AR-SLF-01 | 1 | تنمية ذاتية (category, via 'self-help'); Self Help (description) |
| 3 | As a Man Thinketh | James Allen | EN-SLF-02 | 1 | Self help (category) |

_1 matching title(s) hidden because no copies are available._

## 3. `مقدمه إبن خلدون`

Also searched: ابن خلدون → ibn khaldun

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | مقدّمة ابن خلدون | عبد الرحمن ابن خلدون | AR-HIS-01 | 3 | ابن خلدون (title, author, description); مقدمة ابن (title) |
| 2 | The Muqaddimah (English translation) | Ibn Khaldun | EN-HIS-01 | 1 | Ibn Khaldun (author, description, via 'ابن خلدون') |

## 4. `الف ليلة وليلة`

Also searched: ألف ليلة وليلة → arabian nights, thousand and one nights

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | ألف ليلة وليلة | مؤلف مجهول | AR-LIT-01 | 4 | ليلة وليلة (title); ألف ليلة (title) |
| 2 | The Arabian Nights | Anonymous (tr. Richard F. Burton) | EN-LIT-01 | 2 | Arabian Nights (title, via 'ألف ليلة وليلة') |

## 5. `تاريخ الأندلس`

Also searched: تاريخ → history, historical

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | الكامل في التاريخ | ابن الأثير | AR-HIS-03 | 2 | التاريخ (title, category, description) |
| 2 | مقدّمة ابن خلدون | عبد الرحمن ابن خلدون | AR-HIS-01 | 3 | تاريخ (category, description) |
| 3 | طوق الحمامة في الألفة والألاف | ابن حزم الأندلسي | AR-LIT-05 | 2 | الأندلس (description) |
| 4 | A Tale of Two Cities | Charles Dickens | EN-NOV-10 | 1 | Historical (category, via 'تاريخ') |
| 5 | The Muqaddimah (English translation) | Ibn Khaldun | EN-HIS-01 | 1 | History (category, description, via 'تاريخ') |

_1 matching title(s) hidden because no copies are available._

## 6. `history`

Also searched: history → تاريخ, historical

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | The Muqaddimah (English translation) | Ibn Khaldun | EN-HIS-01 | 1 | History (category, description) |
| 2 | A Tale of Two Cities | Charles Dickens | EN-NOV-10 | 1 | Historical (category, via 'history') |
| 3 | الكامل في التاريخ | ابن الأثير | AR-HIS-03 | 2 | التاريخ (title, category, description, via 'history') |
| 4 | مقدّمة ابن خلدون | عبد الرحمن ابن خلدون | AR-HIS-01 | 3 | تاريخ (category, description, via 'history') |

_1 matching title(s) hidden because no copies are available._

## 7. `روايات نجيب محفوظ`

Also searched: روايات → رواية, novel, fiction

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | بين القصرين | نجيب محفوظ | AR-NOV-03 | 2 | نجيب محفوظ (author); رواية (category, via 'روايات') |
| 2 | Frankenstein | Mary Shelley | EN-NOV-02 / EN-DISPLAY | 3 | fiction (category, via 'روايات'); Novel (category, via 'روايات') |
| 3 | موسم الهجرة إلى الشمال | الطيب صالح | AR-NOV-04 | 1 | رواية (category, description, via 'روايات') |
| 4 | The Time Machine | H. G. Wells | EN-NOV-05 | 1 | fiction (category, via 'روايات') |
| 5 | عودة الروح | توفيق الحكيم | AR-NOV-05 | 1 | رواية (category, via 'روايات') |

_7 more in-stock matches beyond the limit; 1 matching title(s) hidden because no copies are available._

## 8. `الغزالى`

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | تهافت الفلاسفة | أبو حامد الغزالي | AR-PHI-02 | 1 | الغزالي (author, description) |
| 2 | تهافت التهافت | ابن رشد | AR-PHI-03 | 1 | الغزالي (description) |
| 3 | إحياء علوم الدين | أبو حامد الغزالي | AR-REL-01 | 2 | الغزالي (author) |

## 9. `sherlok holms`

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | The Adventures of Sherlock Holmes | Arthur Conan Doyle | EN-NOV-04 | 3 | close spelling (character match) |

## 10. `خيال علمي`

Also searched: خيال علمي → science fiction, sci-fi

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | The Time Machine | H. G. Wells | EN-NOV-05 | 1 | Science fiction (category, via 'خيال علمي') |
| 2 | Frankenstein | Mary Shelley | EN-NOV-02 / EN-DISPLAY | 3 | Science fiction (category, via 'خيال علمي') |

## 11. `كتب للأطفال`

Also searched: أطفال → قصص أطفال, children, kids

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | مغامرات أليس في بلاد العجائب | لويس كارول (ترجمة عربية) | AR-KID-01 | 3 | أطفال (category) |
| 2 | The Jungle Book | Rudyard Kipling | EN-KID-03 | 3 | Children (category, via 'أطفال') |
| 3 | Alice's Adventures in Wonderland | Lewis Carroll | EN-KID-01 | 2 | Children (category, via 'أطفال') |
| 4 | Treasure Island | Robert Louis Stevenson | EN-KID-02 | 2 | Children (category, via 'أطفال') |

## 12. `war strategy`

Also searched: war → حرب, استراتيجية, military

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | The Art of War | Sun Tzu (tr. Lionel Giles) | EN-POL-01 | 1 | War (title, description); Strategy (category) |
| 2 | فن الحرب | سون تزو (ترجمة عربية) | AR-POL-02 | 2 | الحرب (title, description, via 'war'); استراتيجية (category, via 'war') |

## 13. `avicenna`

Also searched: avicenna → ابن سينا, ibn sina

| # | Title | Author | Shelf | Copies | Why it matched |
|---|---|---|---|---|---|
| 1 | القانون في الطب | ابن سينا | AR-MED-01 | 1 | ابن سينا (author, via 'avicenna') |

## 14. `Dracula`

No in-stock matches.

_1 matching title(s) hidden because no copies are available._
