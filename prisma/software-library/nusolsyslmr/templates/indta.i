**********************************************************************
*  CLFR100  LEAD COOLING  REACTOR  RELAP5   MODEL                 
*                                2022-05               
**********************************************************************
*  
*
*------------------------------------------------------ 
*      Miscellaneous control cards 
*------------------------------------------------------
*  card 100 problem type and option 
*
*      prblm.type      prblm.option
100    newath          transnt          * stdy-st           *transnt
*
*  card 101 input check or run option 
*      run option
101    run
*
*  card 102 units selection
*      in.units        out.units
102    si               si
*
*  card 103 restart input file control 
*      restart. number  restart. time  
*103 
*
*  card 105 CPU time remaining and diagnostic edit/dump
*      CPU rem.lim1     CPU rem.lim2
*105    100.0            200.0
*
*  card 110 Noncondensable gas species
*      noncond.type
*110    argon
110     air
**
*  card 115 Noncondesable mass fraction
115   1.0
*
*
**    refv        refh    fluid    name 
120   304010000   0.0     bipb    'prim'   0 
**    refv        refh    fluid    name 
121   364010000   0.0     h2o      'secn1'  1
*
****
******************************************************
*      time step control
******************************************************
*
*   end.    min.      max.     control. plot  major  rest.
*   time    time.step time.step option  freq. freq.  freq.
201 500.0  1.0e-10   0.005      3     200   2000   2000
*202 600.0  1.0e-9   0.01       7     50   500   500
*
*-----------------------------------------------------
*
*
*-----------------------------------------------------
*
*-------------------------------------*
*            EDIT VARIABLES
*
*-------------------------------------
301  mflowj       303000000       *side1 total coolant mass flow inlet
302  mflowj       304010000       *side1 coolant mass flow district 1 inlet
*303  mflowj       304020000       *side1 coolant mass flow district 2 inlet
*304  mflowj       304030000       *side1 coolant mass flow district 3 inlet

305  mflowj       341000000       *side2 total steam mass flow inlet  
306  mflowj       343010000       *side2 steam mass flow district 1 inlet  
*307  mflowj       343020000       *side2 steam mass flow district 2 inlet  
*308  mflowj       343030000       *side2 steam mass flow district 3 inlet  

309  tempf        304010000       *total side1 temp inlet
310  tempf        306010000       *side1 temp district 1 inlet
*311  tempf        307010000       *side1 temp district 2 inlet
*312  tempf        308010000       *side1 temp district 3 inlet
313  tempf        306400000       *side1 temp district 1 outlet
*314  tempf        307400000       *side1 temp district 2 outlet
*315  tempf        308400000       *side1 temp district 3 outlet
316  tempf        309010000       *total side1 temp outlet

317  tempf        343010000       *total side2 temp inlet
318  tempf        364010000       *side2 district 3 liquid temp inlet                
319  tempg        364010000       *side2 district 3 steam temp inlet 
*320  tempf        365010000       *side2 district 1 liquid temp inlet                
*321  tempg        365010000       *side2 district 1 steam temp inlet  
*322  tempf        366010000       *side2 district 2 liquid temp inlet                
*323  tempg        366010000       *side2 district 2 steam temp inlet
324  tempf        364400000       *side2 district 3 liquid temp outlet                
325  tempg        364400000       *side2 district 3 steam temp outlet 
*326  tempf        365400000       *side2 district 1 liquid temp outlet                
*327  tempg        365400000       *side2 district 1 steam temp outlet  
*328  tempf        366400000       *side2 district 2 liquid temp outlet                
*329  tempg        366400000       *side2 district 2 steam temp outlet
330  tempf        375010000       *total side2 liquid temp outlet                
331  tempg        375010000       *total side2 steam temp outlet 

332  p            304010000       *side1 inlet pressure                     
333  p            309010000       *side1 outlet pressure

334  p            343010000       *side2 inlet pressure                     
335  p            375010000       *side2 outlet pressure


336  cntrlvar     12              *side1 core power
337  cntrlvar     22              *side2 core power
338  cntrlvar     30              *side1 pressure drop
339  cntrlvar     31              *side2 pressure drop
340  cntrlvar     32              *节流件 pressure drop
341  cntrlvar     33              *螺旋段 pressure drop

342  hvmix        309010000       *side1 inlet pressure                     
343  hvmix        304010000       *side1 outlet pressure

344  hvmix        375010000       *side1 inlet pressure                     
345  hvmix        343010000       *side1 outlet pressure


******************************************************
*

******************************************************
*
*-----------------------------------------------------------
* fuel assembly inlet volume  definition 
*-----------------------------------------------------------
*


*-----------------------------------------------------------一回路热端480℃
3020000  tmd   tmdpvol 
*        area   length  Vol   azi   incli eleva  rough   HD   flag
*                            angle  angle change  wall
3020101  100.0   0.05   0.0   0.0   0.0    0.0   1.6e-6  0.0   00
*        word
*         
3020200   3 
* 
*                 
3020201   0.0    1.0e6   753.15



*-------------------------------------------------------一回路流量1366.4        
3030000      flow2  tmdpjun                                                                     
3030101  302010002  304010001   0                                                               
3030200   1                                                                                     
*                                                                                               
3030201   -1.0      623.09        0.0000  0.0       
3030202    0.0      623.09        0.0000  0.0                                                   
3030203   20.00      623.09        0.0000  0.0
3030204   10000.00   623.09        0.0000  0.0

*-----------------------------------------------------------
*  SG2 inlet
*-----------------------------------------------------------
*
3040000    SG-inlet   branch
*
*          number     jun. i.c.
*          of jun.    control
3040001    1          1
*                                           horiz.     vert.
*          area       length     volume     angle      angle
3040101    0.26106    0.05       0.0        0.0        -90.0              
*
*          elev.                 hydraulic  volume
*          change     rough.     diam.      control
3040102    -0.05       1.6e-6     0.0        00
*
*
*          ebt        press.        qual.
3040200    3          1.0E+06       753.15
*
*          from        to        junction   forward    reverse    junct.
*          connect.    connect.    area      loss coef.  l.c.       flags
3041101    304010002   306010001    0.0      0.0        0.0       1000 
3041201    0.      0.0000       0.0
*
*-----------------------------------------------------------
*306   district 1 of side1
*-----------------------------------------------------------
*
3060000    downcome       pipe
*
*          number
*          of vol.
3060001    40
*
*          volume     volume
*          flow area  number
3060101    0.1371    40            
*
*          junction   junction
*          flow area  number
3060201    0.0        39
*
*          length     volume
*          of vol.    number
3060301    0.0465      40         
*
*          volume     volume
*          of vol.    number
3060401      0.0      40
*
*          horiz.     volume
*          angle      number
3060501      0.0      40
*
*          vert.      volume
*          angle      number
3060601    -90.0      40
*
*          elev.      volume
*          change     number
3060701   -0.0465       40
*
*                     hydr.      volume
*          rough.     diam.      number
3060801    1.6e-6   0.022751        40
*
*          forward    reverse    junction
*          loss coef. loss coef. number
3060901    2.0        2.0        39
*
*          vol cntrl. volume
*          flags      number
3061001    00         40
*
*          junction   junction
*          flags      number
3061101    1000       39
*
*          ebt      press.        temp.
3061201    003    1.0E+06    593.15  0.0 0.0  0.0  20
3061202    003    1.0E+06    593.15  0.0 0.0  0.0  30
3061203    003    1.0E+06    593.15  0.0 0.0  0.0  40

*
*          jun.i.c.
*          contrl
3061300    1
*
*          mass.fl     mass.fv   
3061301    0.0         0.0       0.0     39


*-----------------------------------------------------------
3110000  ibox2   branch
3110001  2          1
3110101  0.1371  0.001  0.0  0.00   -90.0  -0.001   1.6e-6   0.0   0
3110200   3  1.0E+06    593.15   
3111101  306400002  311010001    0.0  0.0  0.0    1000   
3111201  0.0      0.0000       0.0
3112101  311010000  309010001    0.0  0.0  0.0    1000   
3112201  0.0      0.0000       0.0



*-----------------------------------------------------------
3090000  ibox2   branch
3090001  1          1
3090101  0.26106   0.01  0.0  0.00   -90.0  -0.01   1.6e-6   0.0   0
3090200   3  1.0E+06    593.15   
3091101  309010002  310010001    0.0  0.0  0.0    1000   
3091201  0.0      0.0000       0.0


*-----------------------------------------------------------
3100000  tmd   tmdpvol 
*        area   length  Vol   azi   incli eleva  rough   HD   flag
*                            angle  angle change  wall
3100101  100.0   0.05   0.0   0.0   0.0    0.0   1.6e-6    0.0   00
*        word
*         
3100200   3 
* 
*                 
3100201   0.0    1.0e6   593.15*******压力设置大一些，计算稳定一些



*-----------------------------------------------------------
* side2 inlet
*-----------------------------------------------------12.0MPa,280℃给水
3400000  tmd   tmdpvol 
*        area   length  Vol   azi   incli eleva  rough   HD   flag
*                            angle  angle change  wall
3400101  100.0   0.05   0.0   0.0   0.0    0.0   1.6e-6  0.0   00
*        word
*         
3400200   3 
* 
*                 
3400201   -1.0    8.0e6   453.15********压力从出口压力倒算
3400202   0.0     8.0e6   453.15
3400203   1500.0   8.0e6   453.15
3400204   1500.1   8.0e6   453.15

*-----------------------------------------------------------
*tdv340--> junction341 --> pipe342
*-----------------------------------------------------------流量16.75        
*
*                name          type
3410000         asg-1       tmdpjun
*
*                from            to      area    jefvcahs
3410101     340010002     343010001       0.0          0
*
*             control          
3410200             1           
*
*              vari.        mass.fl   mass.fv
3410201           0.0       6.15       00.0           0.0



* 
*-----------------------------------------------------------
* pipe342--> branch343 --> pipe350/351/352
*-----------------------------------------------------------
3430000  obox2   branch
3430001  1          1
3430101  0.00383   0.05   0  0.00   -90.00  -0.05   1.6e-6    0.0   00 
3430200  103        8.0e6        453.15  
3431101  343000000  350000000    0.00383     50.0   50.0    00
3431201  0.0         0.0000    0.0





*-----------------------------------------------------------
* 350pipe downpipe
*-----------------------------------------------------------
*
3500000    downpipe       pipe
*
*          number
*          of vol.
3500001    40
*
*          volume     volume
*          flow area  number
3500101     0.00383     40           
*
*          junction   junction
*          flow area  number
3500201    0.0        39
*
*          length     volume
*          of vol.    number
3500301    0.0465       40         
*
*          volume     volume
*          of vol.    number
3500401      0.0      40
*
*          horiz.     volume
*          angle      number
3500501      0.0      40
*
*          vert.      volume
*          angle      number
3500601    -90.0      40
*
*          elev.      volume
*          change     number
3500701    -0.0465       40
*
*                     hydr.      volume
*          rough.     diam.      number
3500801    1.6e-6     0.0088        40
*
*          forward    reverse    junction
*          loss coef. loss coef. number
3500901    0.2        0.2          39
*
*          vol cntrl. volume
*          flags      number
3501001    00         40
*
*          junction   junction
*          flags      number  
3501101    1000       39
*
*          ebt      press.        temp.
3501201    003    8.0e6       553.15    0.0 0.0  0.0  40
*
*          jun.i.c.
*          contrl
3501300    1
*
*          mass.fl     mass.fv   
3501301   0.0         0.0       0.0     39




*-----------------------------------------------------------
* junction 
*-----------------------------------------------------------     

3600000  obox2   sngljun
3600101  350400002    364010001  0.0   0.0   0.0  0100
3600201  1    0.0      0.0          0.0



*-----------------------------------------------------------
*364   district 1 of side2
*-----------------------------------------------------------
*


*
3640000    HX       pipe
*
3648888  2
3649999  0.548
*          number
*          of vol.
3640001    40
*
*          volume     volume
*          flow area  number
3640101    0.00383    40            
*
*          junction   junction
*          flow area  number
3640201    0.0        39
*
*          length     volume
*          of vol.    number
3640301    0.426125       40         
*
*          volume     volume
*          of vol.    number
3640401      0.0      40
*
*          horiz.     volume
*          angle      number
3640501      0.0      40
*
*          vert.      volume
*          angle      number
3640601    6.27         40
*
*          elev.      volume
*          change     number
3640701    0.0465       40
*
*                     hydr.      volume
*          rough.     diam.      number
3640801    1.6e-6   0.0088        40
*
*          forward    reverse    junction
*          loss coef. loss coef. number
3640901    0.2        0.2        39
*
*          vol cntrl. volume
*          flags      number
3641001    00         40
*
*          junction   junction
*          flags      number
3641101    1000       39
*
*          ebt      press.        temp.
3641201    003    8.0e6    553.15  0.0 0.0  0.0  20
3641202    003    8.0e6    553.15  0.0 0.0  0.0  30
3641203    003    8.0e6    553.15  0.0 0.0  0.0  40
*
*          jun.i.c.
*          contrl
3641300    1
*
*          mass.fl     mass.fv   
3641301    0.0         0.0       0.0     39

3641401    0.012  0.0  1.0  1.0  39



*-----------------------------------------------------------
*  pipe370/371/372--> branch375--> pipe376
*-----------------------------------------------------------

3750000  obox2   branch
3750001  2          1
3750101  0.00383  0.05   0  0.00   90.00  0.05   1.6e-6     0.0   00
3750200  103        7.0e6       659.0
3751101  364400002  375010001    0.0   0.0   0.0    00
3752101  375010002  380000000    0.0   0.0   0.0    00
3751201  0.0         0.0000    0.0
3752201  0.0         0.0000    0.0



3751110    0.0088  0.0  1.0  1.0  
3752110    0.0088  0.0  1.0  1.0



*-----------------------------------------------------------
*  pipe376--> branch379--> tdv380
*-----------------------------------------------------------
*3790000  obox2   branch
*3790001  2          1
*3790101  0.00204     0.05   0  0.00   90.00  0.05   1.6e-6     0.0   00
*3790200  103        7.0e6       659.0
*3791101  375000000  379010001    0.0   0.0   0.0    00
*3792101  379010002  380010001    0.0   0.0   0.0    00
*3791201  0.0     0.0000    0.0
*3792201  0.0     0.0000    0.0


*-----------------------------------------------------------
*tdv340--> junction341 --> pipe342
*-----------------------------------------------------------流量16.75        
*
*                name          type
3790000         asg-1       tmdpjun
*
*                from            to      area    jefvcahs
3790101     375010002     380000000       0.0          0
*
*             control          
3790200             1           
*
*              vari.        mass.fl   mass.fv
3790201           0.0       0.0        00.0           0.0




*-----------------------------------------------------------
* steam outlet
*-----------------------------------------------------------
3800000  tmd   tmdpvol 
*        area   length  Vol   azi   incli eleva  rough   HD   flag
*                            angle  angle change  wall
3800101  100.0   0.05   0.0   0.0   0.0    0.0   1.6e-6    0.0   00
*        word
*         
3800200   2 
* 
*                 
3800201   0.0    7.0e6  1.0       *******出口压力11MPa，过热






************************************************************
***   Heat Structure
************************************************************


*-----------------------------------------------------------
*    Main heat exchanger heat structure  district 1
*-----------------------------------------------------------
*****    num. of   num.  geom. s.s. l.bound  reflood b.v    max.
*****    heat str. mesh  type  flag coord.   flag.   indic0 interval            
13501000  40    5     2     1    0.0044     0      0      32
13501100  0     1
13501101  4     0.006  
13501201  9     4  
13501301  0.0   4  
13501401  700.0  1
13501402  700.0  3
13501403  700.0  5
* boundary condition data                         *************10%堵管率
13501501  364010000   10000        101        1   24.1612875       40
**                                                            
13501601  306400000   -10000        113        1   24.1612875       40                                                      
* source data                                               
13501701  0         0.0        0.0     0.0          40
*
13501800  1
13501801  0.0 10. 10. 0. 0. 0. 0. 1.  3.0  1.1  1.0  40   * 
*
13501900  1
13501901  0.0 10.0 10.0  0.0 0.0 0.0 0.0 1.0 3.0 1.1 1.0 40




 
**************************************************************
***          thermal material properties
**************************************************************
*          Materials   properties  definition
**************************************************************
*
*-------------------------------------------------
*          thermal material properties
*-------------------------------------------------
*
20100900   tbl/fctn   1   1  * SS 316L  used for HX heat transfer material        
*
*-------------------------------------------------
*          Used material for SG heat structure
*-------------------------------------------------
*
* SS 316L
* Thermal Conductivity
20100901  293.15  14.1   
20100902  323.15  14.6   
20100903  348.15  15.0   
20100904  373.15  15.4   
20100905  398.15  15.7
20100906  423.15  16.1
20100907  448.15  16.5
20100908  473.15  16.8
20100909  498.15  17.2
20100910  523.15  17.6
20100911  548.15  17.9
20100912  573.15  18.3
20100913  598.15  18.7
20100914  623.15  19.0
20100915  648.15  19.4
20100916  673.15  19.7
20100917  698.15  20.1
20100918  723.15  20.5
20100919  748.15  20.8
20100920  773.15  21.2
20100921  798.15  21.5
20100922  823.15  21.9
20100923  848.15  22.2
20100924  873.15  22.6
20100925  898.15  22.9
20100926  923.15  23.2
20100927  948.15  23.6
20100928  973.15  23.9
20100929  998.15  24.2
20100930  1023.15 24.6
20100931  1050.15 24.6
20100932  1075.15 24.6
20100933  1100.15 24.6
20100934  1125.15 24.6
20100935  1150.15 24.6
20100936  1175.15 24.6
20100937  1200.15 24.6
20100938  1225.15 24.6
*
* Volumetric Heat Capacity
20100951  293.15  3949579.8
20100952  323.15  4010989.0
20100953  348.15  4065040.7
20100954  373.15  4106666.7
20100955  398.15  4131578.9
20100956  423.15  4170984.5
20100957  448.15  4209183.7
20100958  473.15  4221105.5
20100959  498.15  4246913.6
20100960  523.15  4282238.4
20100961  548.15  4302884.6
20100962  573.15  4336492.9
20100963  598.15  4369158.9
20100964  623.15  4387990.8
20100965  648.15  4419134.4
20100966  673.15  4436936.9
20100967  698.15  4466666.7
20100968  723.15  4505494.5
20100969  748.15  4511930.6
20100970  773.15  4549356.2
20100971  798.15  4555084.7
20100972  823.15  4581590.0
20100973  848.15  4586776.9
20100974  873.15  4612244.9
20100975  898.15  4626262.6
20100976  923.15  4630738.5
20100977  948.15  4654832.3
20100978  973.15  4667968.8
20100979  998.15  4689922.5
20100980  1023.15 4739884.4
20100981  1050.15 4739884.4
20100982  1075.15 4739884.4
20100983  1100.15 4739884.4
20100984  1125.15 4739884.4
20100985  1150.15 4739884.4
20100986  1175.15 4739884.4
20100987  1200.15 4739884.4
20100988  1225.15 4739884.4

*
**-----------------------------------------------------------
*  control card
*-----------------------------------------------------------
*
20501000   hsteam   mult  1.0  0.0  1
20501001    hvmix      309010000
20501002    mflowj     303000000

20501100   hwter   mult  1.0  0.0  1
20501101    hvmix      304010000
20501102    mflowj     303000000


20501200  load1 sum   1.0e-6  1.0   1   *MW
20501201    0.0       -1.0  cntrlvar      10    
20501202               1.0  cntrlvar      11


20502000   Hcout   mult  1.0  0.0  1
20502001    hvmix      375010000
20502002    mflowj     341000000

20502100   hcin   mult  1.0  0.0  1
20502101    hvmix      343010000
20502102    mflowj     341000000


20502200  load2 sum   1.0e-6  1.0   1   *MW
20502201    0.0        -1.0  cntrlvar      21    
20502202                1.0  cntrlvar      20


20503000   DP1   sum 1.0e-6    1.2  1         *MPa，一次侧压降
20503001    0.0        1.0   p    304010000
20503002              -1.0   p    309010000


20503100   DP2   sum 1.0e-6    1.2  1        *MPa，二次侧压降

20503101    0.0        1.0   p    343010000
20503102              -1.0   p    375010000

20503200   DP3   sum 1.0e-6    1.2  1        *MPa，节流件压降
20503201    0.0       -1.0   p    350010000
20503202               1.0   p    343010000

20503300   DP4   sum 1.0e-6    1.2  1        *MPa，螺旋段压降
20503301    0.0        -1.0   p    375010000
20503302                1.0   p    350400000

. end of input
