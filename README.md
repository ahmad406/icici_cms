# ICICI CMS

Generates the ICICI CMS payment upload (PUSH) `.txt` file from Payment Entries.

Install: copy this folder into `apps/`, then
`bench --site <site> install-app icici_cms && bench --site <site> migrate`

Setup: open **ICICI CMS Settings** and set the client code.
Use: **ICICI CMS Batch** > New > Get Payment Entries > Save > Submit. The file is attached to the batch.

Payment Indicator is read from the Mode of Payment name; it must contain NEFT, RTGS, IMPS or IFT.
Record identifiers ("P" payment line, "A" annexure line) are constants at the top of `icici_cms/icici_cms/utils.py`; confirm them with ICICI.
