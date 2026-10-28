
create proc spsae_asigna_oper_fabrica
            @operador       varchar(30),
			@tipo           char(1),
			@num_inventario varchar(20),
            @num_cambio     int,
			@clave_cambio   varchar(30),
			@comentario     varchar(255),
			@fecha_trabajo  datetime,
			@accion         char(1),
			@tipo_preparacion char(1) 
			
as
declare @status int, @num_cambio_equipo int, @hora varchar(5),@f_act datetime 

if ( @num_cambio = -1 ) select @num_cambio = NULL

if ( @tipo = 'P') 
	if not exists ( select 1 from CITA where inv_nuevo = @num_inventario and @fecha_trabajo < fecha_actual ) return 50029
if ( @tipo = 'I' ) 
   begin
   select @hora =  convert(char(5),fecha_actual,108) , @f_act=fecha_actual from CITA where inv_nuevo = @num_inventario 
   
   select @fecha_trabajo =   convert(char(10),@fecha_trabajo,111)+' '+@hora
   
   
   --select @fecha_trabajo, @f_act,dateadd(dd,1,@f_act) 
   
   ---if not exists ( select 1 from CITA where inv_nuevo = @num_inventario  and @fecha_trabajo < dateadd(dd,1,fecha_actual) ) return 50030
   end 
   

if ( @accion = 'A' )
   begin
   if not exists ( select 1 from USUARIO where clave_usuario     = @operador ) return 50014
   if not exists ( select 1 from OPERADOR_FABRICA where operador = @operador and tipo = @tipo ) return 50015
   
   if exists ( select 1 from INSTPREP_EQUIPO where tipo = @tipo and num_cambio = @num_cambio 
                                                             and num_inventario = @num_inventario ) return 50016
															 
   begin tran actINSTPREP_EQUIPO

   insert into INSTPREP_EQUIPO 
               ( operador, tipo, num_cambio, num_inventario, f_cambio, clave_cambio, comentario , fecha_trabajo) 
        values (@operador,@tipo,@num_cambio,@num_inventario,getdate(),@clave_cambio,@comentario, @fecha_trabajo)

    select @status = @@error
	if ( @status <> 0 )
	   begin
	   rollback actINSTPREP_EQUIPO
	   return @status
	   end
	   
   if ( @tipo_preparacion = 'B' ) -- Preparación basica sin revisión
      begin
      update EQUIPO_ASIGNADO set  edo_asignacion = 'C' where num_inventario = @num_inventario
      
	  select @status = @@error
	  if ( @status  <>  0 ) 
	     begin
		 rollback actINSTPREP_EQUIPO
		 return @status
		 end
		 
	  select @num_cambio_equipo = max(isnull(num_cambio,0))+1 from EQUIPO_ESTADO where num_inventario = @num_inventario
      insert into EQUIPO_ESTADO 
	              ( num_cambio, num_inventario, edo_asignacion, f_cambio, clave_cambio ) 
	       values (@num_cambio_equipo,@num_inventario,'C',getdate(),@clave_cambio) 
		   
      select @status = @@error
	  if ( @status  <>  0 ) 
	     begin
		 rollback actINSTPREP_EQUIPO
		 return @status
		 end		
		
      end
   
	
		
   commit  actINSTPREP_EQUIPO
   return 0 
   end
if ( @accion = 'B' ) 
   begin 
   begin tran actINSTPREP_EQUIPO
   
   delete  INSTPREP_EQUIPO
         where operador       = @operador
		   and tipo           = @tipo
		   and num_cambio     = @num_cambio
		   and num_inventario = @num_inventario 
		 
    select @status = @@error
	if ( @status <> 0 )
	   begin
	   rollback actINSTPREP_EQUIPO
	   return @status
	   end		 
   
   commit actINSTPREP_EQUIPO
   end   

if ( @accion = 'C' ) 
   begin 
   begin tran actINSTPREP_EQUIPO
   
   update   INSTPREP_EQUIPO
           set comentario = @comentario,
               fecha_trabajo= @fecha_trabajo
         where operador       = @operador
		   and tipo           = @tipo
		   and num_cambio     = @num_cambio
		   and num_inventario = @num_inventario 
		 
    select @status = @@error
	if ( @status <> 0 )
	   begin
	   rollback actINSTPREP_EQUIPO
	   return @status
	   end		 
   
   commit actINSTPREP_EQUIPO
   end   


return 0 
go 
