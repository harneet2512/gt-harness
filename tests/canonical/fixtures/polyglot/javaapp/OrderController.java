package javaapp;

@Controller
public class OrderController {

    @Autowired
    private OrderService service;

    @GetMapping("/api/orders")
    public String list() {
        return service.findAll();
    }

    @GetMapping("/api/orders/total")
    public String total() {
        return service.total();
    }
}
