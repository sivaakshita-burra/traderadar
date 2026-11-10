      *================================================================
      * SHIPREC - SYNTHETIC IMPORT SHIPMENT RECORD (TRADERADAR SAMPLE)
      * ALL DATA IS INVENTED. NOT BASED ON ANY REAL SYSTEM.
      *================================================================
       01  SHIPMENT-RECORD.
           05  SHP-ID                  PIC X(10).
           05  SHP-STATUS              PIC X(01).
               88  SHP-OPEN            VALUE 'O'.
               88  SHP-CLEARED         VALUE 'C'.
               88  SHP-HELD            VALUE 'H'.
           05  SHP-ORIGIN-CTRY         PIC X(02).
           05  SHP-INCOTERM            PIC X(03).
           05  SHP-CURRENCY            PIC X(03).
           05  SHP-ARRIVAL-DATE        PIC 9(08).
           05  SHP-ARRIVAL-PARTS REDEFINES SHP-ARRIVAL-DATE.
               10  SHP-ARR-YYYY        PIC 9(04).
               10  SHP-ARR-MM          PIC 9(02).
               10  SHP-ARR-DD          PIC 9(02).
           05  SHP-FREIGHT-AMT         PIC S9(7)V99 COMP-3.
           05  SHP-INSURANCE-AMT       PIC S9(5)V99 COMP-3.
           05  SHP-FX-RATE             PIC 9(3)V9(6) COMP-3.
           05  SHP-LINE-COUNT          PIC S9(4) COMP.
           05  SHP-LINE OCCURS 3 TIMES.
               10  LIN-HS-CODE         PIC X(10).
               10  LIN-DESCRIPTION     PIC X(20).
               10  LIN-QUANTITY        PIC S9(7) COMP-3.
               10  LIN-NET-WEIGHT-KG   PIC S9(7)V999 COMP-3.
               10  LIN-UNIT-PRICE      PIC S9(7)V99 COMP-3.
               10  LIN-ADJUSTMENT      PIC S9(5)V99
                                       SIGN LEADING SEPARATE.
           05  FILLER                  PIC X(05).
